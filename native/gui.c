/* 读取原生窗口元数据；只有已定位的控件可取得焦点或填写登录信息。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static ProbeState *active;
static unsigned count;
static int document_pending;
void emit_window(ProbeState *s,HWND w) {
    char cls[80];GetClassNameA(w,cls,80);
    RECT r;GetWindowRect(w,&r);
    LONG style=GetWindowLongA(w,GWL_STYLE);
    int secret=!strcmp(cls,"Edit")&&(style&ES_PASSWORD);
    char text[16385]={0};
    if(!secret){
        if(GetDlgCtrlID(w)==7201)SendMessageA(w,WM_GETTEXT,sizeof(text),(LPARAM)text);
        else GetWindowTextA(w,text,256);
    }
    emit(s,"{\"hwnd\":%lu,\"parent\":%lu,\"root\":%lu,\"checked\":%ld,\"id\":%d,\"class_name\":\"%s\",\"visible\":%s,\"enabled\":%s,\"password\":%s,\"rect\":[%ld,%ld,%ld,%ld],\"text_hex\":\"",
         (DWORD)(uintptr_t)w,(DWORD)(uintptr_t)GetParent(w),(DWORD)(uintptr_t)GetAncestor(w,GA_ROOT),!strcmp(cls,"Button")?(long)SendMessageA(w,BM_GETCHECK,0,0):0,GetDlgCtrlID(w),cls,IsWindowVisible(w)?"true":"false",IsWindowEnabled(w)?"true":"false",secret?"true":"false",r.left,r.top,r.right,r.bottom);
    hex_text(s,text);emit(s,"\",\"items_hex\":[");
    if(!strcmp(cls,"ComboBox")){
        int n=(int)SendMessageA(w,CB_GETCOUNT,0,0);
        for(int i=0;i<n&&i<64;i++){
            if(i){emit(s,",");}
            emit(s,"\"");
            if(SendMessageA(w,CB_GETLBTEXTLEN,i,0)<256){text[0]=0;SendMessageA(w,CB_GETLBTEXT,i,(LPARAM)text);hex_text(s,text);}
            emit(s,"\"");
        }
    }
    emit(s,"]}");
}
static void add_window(HWND w) {
    if(count>=512){active->error=30;return;}
    char cls[80];GetClassNameA(w,cls,80);
    int control_id=GetDlgCtrlID(w);
    int filter=(control_id>=3301&&control_id<=3325)
        ||(control_id>=3400&&control_id<3550)||(control_id>=4100&&control_id<4220);
    if(!IsWindowVisible(w)&&strcmp(cls,"ListCtrl")&&!filter)return;
    if(count++)emit(active,",");
    emit_window(active,w);
}
static BOOL CALLBACK child(HWND w,LPARAM unused){add_window(w);return active->error!=30;}
static BOOL CALLBACK top(HWND w,LPARAM unused){
    DWORD pid;GetWindowThreadProcessId(w,&pid);
    if(pid==active->pid&&IsWindowVisible(w)){
        if(confirm_document(active,w,active->action==MANAGED_WINDOWS||active->action==STARTUP_DONE))document_pending=1;
        add_window(w);EnumChildWindows(w,child,0);
    }
    return active->error!=30;
}

typedef struct {ProbeState *request;HWND main,panel;unsigned matches;} FormScope;
static int valid_panel(FormScope *scope,HWND panel){
    DWORD pid=0,thread=GetWindowThreadProcessId(panel,&pid);
    if(!IsWindow(panel)||pid!=scope->request->pid||thread!=scope->request->gui_thread
        ||panel==scope->main||GetAncestor(panel,GA_ROOT)!=scope->main
        ||!IsWindowVisible(panel)||!IsWindowEnabled(panel)||!GetDlgItem(panel,3320))return 0;
    HWND button=GetDlgItem(panel,3324);WCHAR cls[80]={0},text[80]={0};
    GetClassNameW(button,cls,80);GetWindowTextW(button,text,80);
    return button&&GetParent(button)==panel&&IsWindowVisible(button)&&IsWindowEnabled(button)
        &&!wcscmp(cls,L"Button")&&!wcscmp(text,L"预埋/条件");
}
static BOOL CALLBACK find_panel(HWND w,LPARAM context){
    FormScope *scope=(FormScope *)context;
    if(GetDlgCtrlID(w)==3324&&valid_panel(scope,GetParent(w))){
        scope->panel=GetParent(w);scope->matches++;
    }
    return TRUE;
}
static BOOL CALLBACK form_child(HWND w,LPARAM context){
    HWND panel=(HWND)context,parent=GetParent(w);int id=GetDlgCtrlID(w);
    if(!((id>=3301&&id<=3325)||id==1472))return TRUE;
    if(parent!=panel){
        WCHAR cls[80];GetClassNameW(w,cls,80);
        if(wcscmp(cls,L"Edit")||GetParent(parent)!=panel||GetDlgCtrlID(parent)!=id)return TRUE;
    }
    add_window(w);return active->error!=30;
}
static void form_windows(ProbeState *s,HWND main){
    unsigned long raw=0;char tail;
    if(sscanf(s->argument,"%lu %c",&raw,&tail)!=1){s->error=32;return;}
    FormScope scope={s,main,NULL,0};
    if(GetAncestor(main,GA_ROOT)!=main||!matches_main(s,main)||!IsWindowEnabled(main)){
        emit(s,"{\"valid\":false}");return;
    }
    if(raw){scope.panel=(HWND)(uintptr_t)raw;scope.matches=valid_panel(&scope,scope.panel);}
    else EnumChildWindows(main,find_panel,(LPARAM)&scope);
    if(scope.matches!=1){emit(s,"{\"valid\":false}");return;}
    active=s;count=0;emit(s,"{\"valid\":true,\"windows\":[");
    add_window(main);add_window(scope.panel);EnumChildWindows(scope.panel,form_child,(LPARAM)scope.panel);
    GUITHREADINFO info={0};info.cbSize=sizeof(info);
    if(!GetGUIThreadInfo(s->gui_thread,&info))s->error=30;
    emit(s,"],\"focus\":%lu,\"flags\":%lu,\"capture\":%lu}",
         (DWORD)(uintptr_t)info.hwndFocus,info.flags,(DWORD)(uintptr_t)info.hwndCapture);
    active=NULL;
}
void gui_query(ProbeState *s,HWND window){
    if(s->action==FORM_WINDOWS){form_windows(s,window);return;}
    if(s->action==WINDOWS||s->action==STARTUP_DONE||s->action==MANAGED_WINDOWS){
        reset_notice_state(s);
        active=s;count=0;document_pending=0;emit(s,"{\"windows\":[");EnumWindows(top,0);
        GUITHREADINFO info={0};info.cbSize=sizeof(info);GetGUIThreadInfo(GetCurrentThreadId(),&info);
        emit(s,"],\"focus\":%lu,\"flags\":%lu,\"capture\":%lu,\"document_pending\":%s}",
             (DWORD)(uintptr_t)info.hwndFocus,info.flags,(DWORD)(uintptr_t)info.hwndCapture,document_pending?"true":"false");
        if(s->action==STARTUP_DONE&&!document_pending&&!s->error)s->startup_active=0;
        active=NULL;return;
    }
    if(!IsWindowVisible(window)||!IsWindowEnabled(window)){s->error=31;return;}
    if(s->action==FOCUS){
        SetForegroundWindow(GetAncestor(window,GA_ROOT));SetFocus(window);
        emit(s,"{\"focused\":%s}",GetFocus()==window?"true":"false");return;
    }
    WCHAR title[256];GetWindowTextW(GetAncestor(window,GA_ROOT),title,256);
    char cls[80];GetClassNameA(window,cls,80);
    HWND parent=GetParent(window),panel=GetParent(parent);
    char parent_class[80];GetClassNameA(parent,parent_class,sizeof(parent_class));
    int id=GetDlgCtrlID(window);
    /* 市价填参仅放行标准下单板的三个编辑框，不扩展成任意窗口写入。 */
    int order_edit=s->action==SET_TEXT && matches_main(s,window) && !strcmp(cls,"Edit")
        && GetDlgCtrlID(parent)==id && GetDlgItem(panel,3320) && GetDlgItem(panel,3324)
        && ((id==3301&&!strcmp(parent_class,"Q7InstrumentIDCtrl"))
            ||((id==3302||id==3303)&&!strcmp(parent_class,"Q7CustomCtrl")));
    if(wcscmp(title,L"用户登录")&&!order_edit){s->error=32;return;}
    if(s->action==SET_TEXT){
        if(strcmp(cls,"Edit")&&strcmp(cls,"ComboBox")){s->error=32;return;}
        unsigned char decoded[512];size_t n=strlen(s->argument);
        if(n%2||n/2>=sizeof(decoded)){s->error=32;return;}
        for(size_t i=0;i<n/2;i++){unsigned v;if(sscanf(s->argument+2*i,"%2x",&v)!=1){s->error=32;return;}decoded[i]=(BYTE)v;}
        decoded[n/2]=0;SendMessageA(window,WM_SETTEXT,0,(LPARAM)decoded);SecureZeroMemory(decoded,sizeof(decoded));
    }
    if(s->action==SELECT_COMBO){
        int index=atoi(s->argument),n=(int)SendMessageA(window,CB_GETCOUNT,0,0);
        if(strcmp(cls,"ComboBox")||index<0||index>=n){s->error=32;return;}
        SendMessageA(window,CB_SETCURSEL,index,0);
        SendMessageA(GetParent(window),WM_COMMAND,MAKEWPARAM(GetDlgCtrlID(window),CBN_SELCHANGE),(LPARAM)window);
    }
    emit(s,"{\"ok\":true}");
}
