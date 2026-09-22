/* 已识别文档确认；运行期结算单仅在执行器持有 GUI 执行权时处理。 */
#include "probe.h"
#include <richedit.h>
#include <stdlib.h>
#include <wchar.h>

static int button(HWND window,const WCHAR *first,const WCHAR *second,int enabled){
    if(!window||(enabled&&!IsWindowEnabled(window)))return 0;
    WCHAR cls[64],text[128];GetClassNameW(window,cls,64);GetWindowTextW(window,text,128);
    return !wcscmp(cls,L"Button")&&(!wcscmp(text,first)||!wcscmp(text,second));
}

static int settlement_loaded(HWND body){
    /* 显式读取 Unicode，避免 RichEdit20A 的 ANSI 消息转换截断中文正文。 */
    GETTEXTLENGTHEX measure={GTL_PRECISE|GTL_NUMCHARS,1200};
    LRESULT length=SendMessageW(body,EM_GETTEXTLENGTHEX,(WPARAM)&measure,0);
    if(length<128||length>524288)return 0;
    size_t bytes=((size_t)length+1)*sizeof(WCHAR);
    WCHAR *text=calloc((size_t)length+1,sizeof(WCHAR));if(!text)return 0;
    GETTEXTEX request={(DWORD)bytes,GT_DEFAULT,1200,NULL,NULL};
    LRESULT read=SendMessageW(body,EM_GETTEXTEX,(WPARAM)&request,(LPARAM)text);
    int ready=read==length&&wcsstr(text,L"结算单")&&wcsstr(text,L"客户号")
        &&wcsstr(text,L"资金状况")&&wcsstr(text,L"Client ID");
    SecureZeroMemory(text,bytes);free(text);return ready;
}

int confirm_document(ProbeState *s,HWND window,int allow_settlement){
    if(!s)return 0;
    int startup=s->startup_active&&GetTickCount64()<=s->startup_until;
    if(s->startup_active&&!startup)return 0;
    if(!startup&&!allow_settlement)return 0;
    DWORD pid;DWORD thread=GetWindowThreadProcessId(window,&pid);
    if(pid!=s->pid||thread!=s->gui_thread||GetAncestor(window,GA_ROOT)!=window)return 0;
    WCHAR cls[64],title[128];GetClassNameW(window,cls,64);GetWindowTextW(window,title,128);
    if(wcscmp(cls,L"#32770"))return 0;
    DWORD kind=0;
    if(!wcscmp(title,L"快期隐私政策")||!wcscmp(title,L"隐私政策"))kind=1;
    if(!wcscmp(title,L"快期用户协议")||!wcscmp(title,L"快期软件使用协议"))kind=2;
    if(!wcscmp(title,L"快速配置向导"))kind=3;
    if(!wcscmp(title,L"确认结算单"))kind=4;
    if(!kind||(kind==4&&!allow_settlement)||(!startup&&kind!=4))return 0;
    HWND confirm=GetDlgItem(window,IDOK),cancel=GetDlgItem(window,IDCANCEL);
    int command=IDOK;
    if(kind==3){
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
    if(kind==4){
        HWND owner=GetWindow(window,GW_OWNER),body=GetDlgItem(window,7601);
        DWORD owner_pid=0;DWORD owner_thread=GetWindowThreadProcessId(owner,&owner_pid);
        if(!owner||owner_pid!=s->pid||owner_thread!=s->gui_thread||!matches_main(s,owner)
            ||GetParent(confirm)!=window||GetParent(cancel)!=window||GetParent(body)!=window
            ||!IsWindowVisible(window)||!IsWindowVisible(body)||!IsWindowVisible(confirm)||!IsWindowVisible(cancel)
            ||!(GetWindowLongW(body,GWL_STYLE)&ES_READONLY))return 0;
        if(!settlement_loaded(body))return 1;
    }
    if(s->startup_last_window==(DWORD)(uintptr_t)window&&s->startup_last_kind==kind)return 1;
    if(!IsWindowEnabled(window)||!IsWindowEnabled(confirm)||!IsWindowEnabled(cancel)
        ||s->startup_privacy_count+s->startup_terms_count+s->startup_wizard_count+s->settlement_count>=8)return 1;
    /* 异步投递给已核对的对话框，避免在窗口创建/激活回调内重入按钮处理。 */
    if(PostMessageW(window,WM_COMMAND,MAKEWPARAM(command,BN_CLICKED),(LPARAM)confirm)){
        s->startup_last_window=(DWORD)(uintptr_t)window;s->startup_last_kind=kind;
        if(kind==1)s->startup_privacy_count++;
        else if(kind==2)s->startup_terms_count++;
        else if(kind==3)s->startup_wizard_count++;
        else s->settlement_count++;
    }
    return 1;
}
