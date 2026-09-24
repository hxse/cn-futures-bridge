/* 只扫描可见顶层窗口；健康主窗口不读取子控件、下拉选项或资金正文。 */
#include "probe.h"
#include <wchar.h>

#define MENU_FLAGS (GUI_INMENUMODE|GUI_SYSTEMMENUMODE|GUI_POPUPMENUMODE)
typedef struct {
    ProbeState *request;
    HWND tops[128],main,funds;
    unsigned count,mains,dialogs,unknown,funds_count;
    int pending,menu_owned;
    GUITHREADINFO info;
} Readiness;

static int same_thread(ProbeState *s,HWND w){
    DWORD pid=0,thread=GetWindowThreadProcessId(w,&pid);
    return pid==s->pid&&thread==s->gui_thread;
}
static BOOL CALLBACK collect(HWND w,LPARAM value){
    Readiness *r=(Readiness *)value;
    DWORD pid=0;GetWindowThreadProcessId(w,&pid);
    if(pid!=r->request->pid||!IsWindowVisible(w))return TRUE;
    if(r->count>=128){r->request->error=72;return FALSE;}
    r->tops[r->count++]=w;
    if(same_thread(r->request,w)&&matches_main(r->request,w)){
        r->mains++;r->main=w;
    }
    return TRUE;
}
static int funds_dialog(Readiness *r,HWND w){
    WCHAR cls[64],title[128];
    GetClassNameW(w,cls,64);GetWindowTextW(w,title,128);
    if(wcscmp(cls,L"#32770")||wcscmp(title,L"期货资金账户详情")
        ||!same_thread(r->request,w)||GetWindow(w,GW_OWNER)!=r->main
        ||!IsWindowEnabled(w)||!(GetWindowLongW(w,GWL_STYLE)&WS_SYSMENU))return 0;
    HWND body=GetDlgItem(w,7201);
    return body&&GetParent(body)==w&&IsWindowVisible(body);
}
static int menu_belongs_to_main(Readiness *r){
    HWND w=r->info.hwndMenuOwner;
    for(int depth=0;w&&depth<8;depth++){
        if(!same_thread(r->request,w))return 0;
        if(GetAncestor(w,GA_ROOT)==r->main)return 1;
        /* Wine 的菜单归属句柄可为 #32768；沿菜单 owner 链核对真实主窗。 */
        WCHAR cls[64];GetClassNameW(w,cls,64);
        if(wcscmp(cls,L"#32768"))return 0;
        w=GetWindow(w,GW_OWNER);
    }
    return 0;
}
static void scan(Readiness *r){
    reset_notice_state(r->request);
    EnumWindows(collect,(LPARAM)r);
    if(r->request->error)return;
    r->info.cbSize=sizeof(r->info);
    if(!GetGUIThreadInfo(r->request->gui_thread,&r->info)){r->request->error=72;return;}
    for(unsigned i=0;i<r->count;i++){
        HWND w=r->tops[i];WCHAR cls[64];GetClassNameW(w,cls,64);
        if(wcscmp(cls,L"#32770"))continue;
        r->dialogs++;
        if(confirm_document(r->request,w,1)){r->pending=1;continue;}
        if(r->mains==1&&funds_dialog(r,w)){r->funds=w;r->funds_count++;}
        else r->unknown++;
    }
    r->menu_owned=r->mains==1&&menu_belongs_to_main(r);
}
void readiness_query(ProbeState *s,HWND window){
    Readiness r={0};r.request=s;scan(&r);
    if(s->error)return;
    if(s->action==GUI_RECOVER){
        /* 重新扫描后才操作目标，拒绝未知确认、异线程窗口及变化后的归属。 */
        if(r.mains!=1||r.unknown||r.pending){s->error=72;return;}
        const char *action;
        if(window==r.main&&(r.info.flags&MENU_FLAGS)&&r.menu_owned&&!r.dialogs){
            if(!EndMenu()){s->error=72;return;}
            action="menu";
        }else if(window==r.funds&&r.funds_count==1&&r.dialogs==1
                &&!(r.info.flags&(MENU_FLAGS|GUI_INMOVESIZE))&&!r.info.hwndCapture){
            if(!PostMessageW(window,WM_CLOSE,0,0)){s->error=72;return;}
            action="funds";
        }else{s->error=72;return;}
        emit(s,"{\"action\":\"%s\",\"target\":%lu}",action,(DWORD)(uintptr_t)window);
        return;
    }
    unsigned modifiers=0;
    const int keys[]={VK_LSHIFT,VK_RSHIFT,VK_LCONTROL,VK_RCONTROL,VK_LMENU,VK_RMENU};
    for(unsigned i=0;i<6;i++)if(GetAsyncKeyState(keys[i])&0x8000)modifiers|=1u<<i;
    emit(s,"{\"main\":%lu,\"main_count\":%u,\"enabled\":%s,\"focus\":%lu,\"flags\":%lu,"
           "\"capture\":%lu,\"menu_owned\":%s,\"modifiers\":%u,\"dialogs\":%u,"
           "\"unknown_dialogs\":%u,\"funds\":%lu,\"funds_count\":%u,\"document_pending\":%s}",
        (DWORD)(uintptr_t)r.main,r.mains,r.main&&IsWindowEnabled(r.main)?"true":"false",
        (DWORD)(uintptr_t)r.info.hwndFocus,r.info.flags,(DWORD)(uintptr_t)r.info.hwndCapture,
        r.menu_owned?"true":"false",modifiers,r.dialogs,r.unknown,(DWORD)(uintptr_t)r.funds,
        r.funds_count,r.pending?"true":"false");
}
