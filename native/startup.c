/* 已识别文档和附属网页；运行期操作仅由持有 GUI 执行权的扫描触发。 */
#include "probe.h"
#include <richedit.h>
#include <limits.h>
#include <stdlib.h>
#include <wchar.h>

static int button(HWND window,const WCHAR *first,const WCHAR *second,int enabled){
    if(!window||(enabled&&!IsWindowEnabled(window)))return 0;
    WCHAR cls[64],text[128];GetClassNameW(window,cls,64);GetWindowTextW(window,text,128);
    return !wcscmp(cls,L"Button")&&(!wcscmp(text,first)||!wcscmp(text,second));
}

static unsigned handled_count(const ProbeState *s){
    return s->startup_privacy_count+s->startup_terms_count+s->startup_wizard_count
        +s->settlement_count+s->information_close_count;
}

static unsigned notice_number(const WCHAR **cursor){
    unsigned result=0;
    while(**cursor>=L'0'&&**cursor<=L'9'){
        unsigned digit=(unsigned)(**cursor-L'0');
        if(result>((unsigned)INT_MAX-digit)/10)return 0;
        result=result*10+digit;(*cursor)++;
    }
    return result;
}

static int notice_title(const WCHAR *title,const WCHAR *name){
    if(!wcscmp(title,name))return 1;
    if(*title++!=L'(')return 0;
    unsigned current=notice_number(&title);
    if(!current||*title++!=L'/')return 0;
    unsigned total=notice_number(&title);
    return total>=current&&title[0]==L')'&&title[1]==L' '&&!wcscmp(title+2,name);
}

static int trade_notice(ProbeState *s,HWND window){
    HWND confirm=GetDlgItem(window,IDOK),check=GetDlgItem(window,1299),body=GetDlgItem(window,7501);
    HWND previous=GetDlgItem(window,1245),next=GetDlgItem(window,1246);
    WCHAR cls[64]={0},text[2048]={0};GetClassNameW(body,cls,64);
    if(GetDlgItem(window,IDCANCEL)||!button(confirm,L"确定",L"确定",0)
        ||!button(check,L"不再提示成交通知",L"不再提示成交通知",0)
        ||!button(previous,L"上一条",L"上一条",0)||!button(next,L"下一条",L"下一条",0)
        ||GetParent(confirm)!=window||GetParent(check)!=window||GetParent(body)!=window
        ||GetParent(previous)!=window||GetParent(next)!=window||wcscmp(cls,L"Static")
        ||(GetWindowLongW(check,GWL_STYLE)&BS_TYPEMASK)!=BS_OWNERDRAW
        ||!IsWindowVisible(confirm)||!IsWindowVisible(check)||!IsWindowVisible(body))return 0;
    int length=GetWindowTextLengthW(body);
    if(length==0)return 1;
    if(length>=2048||GetWindowTextW(body,text,2048)<=0)return 0;
    int matches=wcsstr(text,L"合约")&&wcsstr(text,L"买卖")&&wcsstr(text,L"开平")
        &&wcsstr(text,L"成交量")&&wcsstr(text,L"成交价");
    SecureZeroMemory(text,sizeof(text));if(!matches)return 0;
    if(s->trade_notice_window&&(HWND)(uintptr_t)s->trade_notice_window!=window
        &&IsWindowVisible((HWND)(uintptr_t)s->trade_notice_window))return 1;
    if(s->trade_notice_window!=(DWORD)(uintptr_t)window){
        s->trade_notice_window=(DWORD)(uintptr_t)window;s->trade_notice_phase=0;
    }
    if(s->trade_notice_phase==3)return 1;
    if(!IsWindowEnabled(window)||!IsWindowEnabled(confirm))return 1;
    LRESULT checked=SendMessageW(check,BM_GETCHECK,0,0);
    if(s->trade_notice_phase==0){
        if(checked==BST_UNCHECKED){
            if(IsWindowEnabled(check)&&PostMessageW(check,BM_CLICK,0,0)){
                s->trade_notice_count++;s->trade_notice_check_count++;s->trade_notice_phase=1;
            }
            return 1;
        }
        if(checked!=BST_CHECKED)return 1;
        s->trade_notice_count++;s->trade_notice_phase=1;
    }
    /* 回读勾选成功才允许确认；未生效时等待，不重复点击造成反向切换。 */
    if(checked!=BST_CHECKED)return 1;
    if(s->trade_notice_phase==1){s->trade_notice_checked_count++;s->trade_notice_phase=2;}
    if(PostMessageW(window,WM_COMMAND,MAKEWPARAM(IDOK,BN_CLICKED),(LPARAM)confirm)){
        s->trade_notice_confirm_count++;s->trade_notice_phase=3;
    }
    return 1;
}

static int order_notice(ProbeState *s,HWND window,DWORD kind){
    HWND confirm=GetDlgItem(window,IDOK),check=GetDlgItem(window,1299),body=GetDlgItem(window,7501);
    WCHAR cls[64],text[1024];GetClassNameW(body,cls,64);
    const WCHAR *labels[]={L"不再提示下单失败",L"不再提示下单成功",
                           L"不再提示撤单成功(即时单)",L"不再提示撤单失败(即时单)",
                           L"不再提示撤单成功",L"不再提示撤单失败"};
    if(GetDlgItem(window,IDCANCEL)||!button(confirm,L"确定",L"确定",0)
        ||!button(check,labels[kind-7],labels[kind-7],0)
        ||GetParent(confirm)!=window||GetParent(check)!=window||GetParent(body)!=window
        ||wcscmp(cls,L"Static")||!IsWindowVisible(body)||!IsWindowVisible(confirm)||!IsWindowVisible(check))return 0;
    int length=GetWindowTextLengthW(body);
    if(length==0)return 1;
    if(length>=1024||GetWindowTextW(body,text,1024)<=0)return 0;
    int matched=wcsstr(text,L"合约：")&&wcsstr(text,L"买卖：")&&wcsstr(text,L"开平：")
        &&wcsstr(text,L"手数：")&&wcsstr(text,L"价格：")&&wcsstr(text,L"备注：");
    SecureZeroMemory(text,sizeof(text));if(!matched)return 0;
    if(s->startup_last_kind==kind&&s->startup_last_window==(DWORD)(uintptr_t)window)return 1;
    if(!IsWindowEnabled(window)||!IsWindowEnabled(confirm))return 1;
    /* 运行期通知由基线期限及窗口去重限制，不耗尽启动文档的会话预算。 */
    GetWindowTextA(body,s->order_notice_text,sizeof(s->order_notice_text));
    if(PostMessageW(window,WM_COMMAND,MAKEWPARAM(IDOK,BN_CLICKED),(LPARAM)confirm)){
        s->startup_last_kind=kind;s->startup_last_window=(DWORD)(uintptr_t)window;
        s->order_notice_kind=kind;s->order_notice_count++;
    }
    return 1;
}

typedef struct { ProbeState *state;HWND owner;unsigned matches;int success; } SettlementStatus;
static BOOL CALLBACK settlement_status(HWND w,LPARAM value){
    SettlementStatus *status=(SettlementStatus *)value;
    if(GetDlgCtrlID(w)!=1257||!IsWindowVisible(w))return TRUE;
    DWORD pid=0,thread=GetWindowThreadProcessId(w,&pid);
    WCHAR cls[64],text[128];GetClassNameW(w,cls,64);
    if(pid!=status->state->pid||thread!=status->state->gui_thread
        ||GetAncestor(w,GA_ROOT)!=status->owner||wcscmp(cls,L"Static"))return TRUE;
    status->matches++;
    int length=GetWindowTextW(w,text,128);
    /* 固定状态控件格式为 HH:MM:SS、Tab、消息；不把包含成功字样的任意正文当完成证据。 */
    if(length<10||text[2]!=L':'||text[5]!=L':'||text[8]!=L'\t')return TRUE;
    const unsigned digits[]={0,1,3,4,6,7};
    for(unsigned i=0;i<6;i++)if(text[digits[i]]<L'0'||text[digits[i]]>L'9')return TRUE;
    if((text[0]-L'0')*10+text[1]-L'0'>23||(text[3]-L'0')*10+text[4]-L'0'>59
        ||(text[6]-L'0')*10+text[7]-L'0'>59)return TRUE;
    while(length>9&&(text[length-1]==L'\r'||text[length-1]==L'\n'||text[length-1]==L' '))text[--length]=0;
    status->success=!wcscmp(text+9,L"查询结算单成功");
    return TRUE;
}
static int empty_settlement_ready(ProbeState *s,HWND window){
    const WCHAR *title=L"快期2-CTP-上期技术-全天站点";
    if((wcscmp(s->main_titles[0],title)&&wcscmp(s->main_titles[1],title))
        ||!IsWindowEnabled(window)||!IsWindowEnabled(GetDlgItem(window,IDOK))
        ||!IsWindowEnabled(GetDlgItem(window,IDCANCEL)))goto unavailable;
    SettlementStatus status={s,GetWindow(window,GW_OWNER),0,0};
    EnumChildWindows(status.owner,settlement_status,(LPARAM)&status);
    if(status.matches!=1||!status.success)goto unavailable;
    DWORD id=(DWORD)(uintptr_t)window;
    if(s->empty_settlement_window!=id){
        s->empty_settlement_window=id;s->empty_settlement_since=GetTickCount64();return 0;
    }
    return GetTickCount64()-s->empty_settlement_since>=250;
unavailable:
    s->empty_settlement_window=0;s->empty_settlement_since=0;return 0;
}
static int settlement_loaded(ProbeState *s,HWND window,HWND body){
    /* 显式读取 Unicode，避免 RichEdit20A 的 ANSI 消息转换截断中文正文。 */
    GETTEXTLENGTHEX measure={GTL_PRECISE|GTL_NUMCHARS,1200};
    LRESULT length=SendMessageW(body,EM_GETTEXTLENGTHEX,(WPARAM)&measure,0);
    if(length==0){
        if(empty_settlement_ready(s,window))return 2;
        return 0;
    }
    s->empty_settlement_window=0;s->empty_settlement_since=0;
    if(length<128||length>524288)return 0;
    size_t bytes=((size_t)length+1)*sizeof(WCHAR);
    WCHAR *text=calloc((size_t)length+1,sizeof(WCHAR));if(!text)return 0;
    GETTEXTEX request={(DWORD)bytes,GT_DEFAULT,1200,NULL,NULL};
    LRESULT read=SendMessageW(body,EM_GETTEXTEX,(WPARAM)&request,(LPARAM)text);
    int ready=read==length&&wcsstr(text,L"结算单")&&wcsstr(text,L"客户号")
        &&wcsstr(text,L"资金状况")&&wcsstr(text,L"Client ID");
    SecureZeroMemory(text,bytes);free(text);return ready;
}

void reset_notice_state(ProbeState *s){
    if(s->trade_notice_window&&!IsWindowVisible((HWND)(uintptr_t)s->trade_notice_window)){
        if(s->trade_notice_phase==3)s->trade_notice_closed_count++;
        s->trade_notice_window=0;s->trade_notice_phase=0;
    }
    /* 快期可能隐藏并复用句柄；全量读取和轻量检查共享去重状态。 */
    if((s->startup_last_kind==4||s->startup_last_kind==5||(s->startup_last_kind>=7&&s->startup_last_kind<=12))
            &&!IsWindowVisible((HWND)(uintptr_t)s->startup_last_window)){
        s->startup_last_window=0;s->startup_last_kind=0;
    }
    if(s->empty_settlement_window&&!IsWindowVisible((HWND)(uintptr_t)s->empty_settlement_window)){
        s->empty_settlement_window=0;s->empty_settlement_since=0;
    }
}

int confirm_document(ProbeState *s,HWND window,int allow_runtime){
    if(!s)return 0;
    int startup=s->startup_active&&GetTickCount64()<=s->startup_until;
    if(s->startup_active&&!startup)return 0;
    if(!startup&&!allow_runtime)return 0;
    DWORD pid;DWORD thread=GetWindowThreadProcessId(window,&pid);
    if(pid!=s->pid||thread!=s->gui_thread||GetAncestor(window,GA_ROOT)!=window)return 0;
    WCHAR cls[64],title[128];GetClassNameW(window,cls,64);
    int title_length=GetWindowTextLengthW(window);
    /* ANSI 窗口的长度是缓冲区上界，中文标题可能估为 8、实际读取为 4。 */
    if(title_length>=128||GetWindowTextW(window,title,128)<=0)return 0;
    if(wcscmp(cls,L"#32770"))return 0;
    DWORD kind=0;
    if(!wcscmp(title,L"快期隐私政策")||!wcscmp(title,L"隐私政策"))kind=1;
    if(!wcscmp(title,L"快期用户协议")||!wcscmp(title,L"快期软件使用协议"))kind=2;
    if(!wcscmp(title,L"快速配置向导"))kind=3;
    if(!wcscmp(title,L"确认结算单"))kind=4;
    if(!wcscmp(title,L"保证金监控中心"))kind=5;
    if(notice_title(title,L"成交通知"))kind=6;
    if(notice_title(title,L"下单失败"))kind=7;
    if(notice_title(title,L"下单成功"))kind=8;
    if(notice_title(title,L"撤单成功(即时单)"))kind=9;
    if(notice_title(title,L"撤单失败(即时单)"))kind=10;
    if(notice_title(title,L"撤单成功"))kind=11;
    if(notice_title(title,L"撤单失败"))kind=12;
    if(!kind||(kind>=4&&!allow_runtime)||(!startup&&kind<4))return 0;
    if(kind>=4){
        HWND owner=GetWindow(window,GW_OWNER);
        DWORD owner_pid=0;DWORD owner_thread=GetWindowThreadProcessId(owner,&owner_pid);
        if(!owner||owner_pid!=s->pid||owner_thread!=s->gui_thread
            ||!matches_main(s,owner)||!IsWindowVisible(window))return 0;
    }
    if(kind==6)return trade_notice(s,window);
    if(kind>=7&&kind<=12)return order_notice(s,window,kind);
    HWND confirm=GetDlgItem(window,IDOK),cancel=GetDlgItem(window,IDCANCEL);
    int command=IDOK;
    if(kind==5){
        HWND body=GetDlgItem(window,1224);
        GetClassNameW(body,cls,64);
        if(confirm||cancel||!body||GetParent(body)!=window||!IsWindowVisible(body)
            ||wcscmp(cls,L"AtlAxWinLic100")||!(GetWindowLongW(window,GWL_STYLE)&WS_SYSMENU))return 0;
    }else if(kind==3){
        if(!button(cancel,L"取消",L"取消",0)
            ||!button(GetDlgItem(window,1246),L"下一步 >",L"下一步 >",0)
            ||!button(GetDlgItem(window,1247),L"完成",L"完成",0))return 0;
        GetClassNameW(GetDlgItem(window,1224),cls,64);
        if(wcscmp(cls,L"AtlAxWinLic100"))return 0;
        confirm=cancel;command=IDCANCEL;
    }else{
        if(!button(confirm,L"确认",kind==4?L"确认":L"同意",0)
            ||!button(cancel,L"取消",kind==4?L"取消":L"不同意",0))return 0;
        GetClassNameW(GetDlgItem(window,7601),cls,64);
        if(wcscmp(cls,L"RichEdit20A"))return 0;
    }
    int settlement_kind=0;
    if(kind==4){
        HWND body=GetDlgItem(window,7601);
        if(GetParent(confirm)!=window||GetParent(cancel)!=window||GetParent(body)!=window
            ||!IsWindowVisible(window)||!IsWindowVisible(body)||!IsWindowVisible(confirm)||!IsWindowVisible(cancel)
            ||!(GetWindowLongW(body,GWL_STYLE)&ES_READONLY))return 0;
        settlement_kind=settlement_loaded(s,window,body);
        if(!settlement_kind)return 1;
    }
    if(s->startup_last_window==(DWORD)(uintptr_t)window&&s->startup_last_kind==kind)return 1;
    if(!IsWindowEnabled(window)||(kind!=5&&(!IsWindowEnabled(confirm)||!IsWindowEnabled(cancel)))
        ||handled_count(s)>=8)return 1;
    /* 异步投递给已核对的对话框，避免在窗口创建/激活回调内重入按钮处理。 */
    BOOL posted=kind==5?PostMessageW(window,WM_CLOSE,0,0)
        :PostMessageW(window,WM_COMMAND,MAKEWPARAM(command,BN_CLICKED),(LPARAM)confirm);
    if(posted){
        s->startup_last_window=(DWORD)(uintptr_t)window;s->startup_last_kind=kind;
        if(kind==1)s->startup_privacy_count++;
        else if(kind==2)s->startup_terms_count++;
        else if(kind==3)s->startup_wizard_count++;
        else if(kind==4){s->settlement_count++;if(settlement_kind==2)s->empty_settlement_count++;}
        else s->information_close_count++;
    }
    return 1;
}
