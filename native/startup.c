/* 只处理启动阶段的已识别文档确认，所有事件仍在同一终端 GUI 线程。 */
#include "probe.h"
#include <wchar.h>

static int button(HWND window,const WCHAR *first,const WCHAR *second,int enabled){
    if(!window||(enabled&&!IsWindowEnabled(window)))return 0;
    WCHAR cls[64],text[128];GetClassNameW(window,cls,64);GetWindowTextW(window,text,128);
    return !wcscmp(cls,L"Button")&&(!wcscmp(text,first)||!wcscmp(text,second));
}

void confirm_startup(ProbeState *s,HWND window){
    if(!s||!s->startup_active||GetTickCount64()>s->startup_until
        ||s->startup_privacy_count+s->startup_terms_count+s->startup_wizard_count>=8)return;
    DWORD pid;GetWindowThreadProcessId(window,&pid);
    if(pid!=s->pid||GetAncestor(window,GA_ROOT)!=window)return;
    WCHAR cls[64],title[128];GetClassNameW(window,cls,64);GetWindowTextW(window,title,128);
    if(wcscmp(cls,L"#32770"))return;
    DWORD kind=0;
    if(!wcscmp(title,L"快期隐私政策")||!wcscmp(title,L"隐私政策"))kind=1;
    if(!wcscmp(title,L"快期用户协议")||!wcscmp(title,L"快期软件使用协议"))kind=2;
    if(!wcscmp(title,L"快速配置向导"))kind=3;
    if(!kind||(s->startup_last_window==(DWORD)(uintptr_t)window&&s->startup_last_kind==kind))return;
    HWND confirm=GetDlgItem(window,IDOK),cancel=GetDlgItem(window,IDCANCEL);
    int command=IDOK;
    if(kind==3){
        if(!button(cancel,L"取消",L"取消",1)
            ||!button(GetDlgItem(window,1246),L"下一步 >",L"下一步 >",0)
            ||!button(GetDlgItem(window,1247),L"完成",L"完成",0))return;
        GetClassNameW(GetDlgItem(window,1224),cls,64);
        if(wcscmp(cls,L"AtlAxWinLic100"))return;
        confirm=cancel;command=IDCANCEL;
    }else{
        if(!button(confirm,L"确认",L"同意",1)||!button(cancel,L"取消",L"不同意",1))return;
        GetClassNameW(GetDlgItem(window,7601),cls,64);
        if(wcscmp(cls,L"RichEdit20A"))return;
    }
    /* 异步投递给已核对的对话框，避免在窗口创建/激活回调内重入按钮处理。 */
    if(PostMessageW(window,WM_COMMAND,MAKEWPARAM(command,BN_CLICKED),(LPARAM)confirm)){
        s->startup_last_window=(DWORD)(uintptr_t)window;s->startup_last_kind=kind;
        if(kind==1)s->startup_privacy_count++;
        else if(kind==2)s->startup_terms_count++;
        else s->startup_wizard_count++;
    }
}
