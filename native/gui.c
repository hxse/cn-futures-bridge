/* 读取原生窗口元数据；只有已定位的控件可取得焦点或填写登录信息。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static ProbeState *active;
static unsigned count;
static void add_window(HWND w) {
    if(count>=512){active->error=30;return;}
    char cls[80];GetClassNameA(w,cls,80);
    int control_id=GetDlgCtrlID(w);
    int filter=(control_id>=3400&&control_id<3550)||(control_id>=4100&&control_id<4220);
    if(!IsWindowVisible(w) && strcmp(cls,"ListCtrl") && !filter)return;
    if(count++)emit(active,",");
    RECT r;GetWindowRect(w,&r);
    LONG style=GetWindowLongA(w,GWL_STYLE);
    int secret=!strcmp(cls,"Edit")&&(style&ES_PASSWORD);
    char text[16385]={0};
    if(!secret){
        if(GetDlgCtrlID(w)==7201)SendMessageA(w,WM_GETTEXT,sizeof(text),(LPARAM)text);
        else GetWindowTextA(w,text,256);
    }
    emit(active,"{\"hwnd\":%lu,\"parent\":%lu,\"root\":%lu,\"checked\":%ld,\"id\":%d,\"class_name\":\"%s\",\"visible\":%s,\"enabled\":%s,\"password\":%s,\"rect\":[%ld,%ld,%ld,%ld],\"text_hex\":\"",
         (DWORD)(uintptr_t)w,(DWORD)(uintptr_t)GetParent(w),(DWORD)(uintptr_t)GetAncestor(w,GA_ROOT),!strcmp(cls,"Button")?(long)SendMessageA(w,BM_GETCHECK,0,0):0,GetDlgCtrlID(w),cls,IsWindowVisible(w)?"true":"false",IsWindowEnabled(w)?"true":"false",secret?"true":"false",r.left,r.top,r.right,r.bottom);
    hex_text(active,text);emit(active,"\",\"items_hex\":[");
    if(!strcmp(cls,"ComboBox")){
        int n=(int)SendMessageA(w,CB_GETCOUNT,0,0);
        for(int i=0;i<n&&i<64;i++){
            if(i){emit(active,",");}
            emit(active,"\"");
            if(SendMessageA(w,CB_GETLBTEXTLEN,i,0)<256){text[0]=0;SendMessageA(w,CB_GETLBTEXT,i,(LPARAM)text);hex_text(active,text);}
            emit(active,"\"");
        }
    }
    emit(active,"]}");
}
static BOOL CALLBACK child(HWND w,LPARAM unused){add_window(w);return active->error!=30;}
static BOOL CALLBACK top(HWND w,LPARAM unused){
    DWORD pid;GetWindowThreadProcessId(w,&pid);
    if(pid==active->pid&&IsWindowVisible(w)){add_window(w);EnumChildWindows(w,child,0);}
    return active->error!=30;
}
void gui_query(ProbeState *s,HWND window){
    if(s->action==WINDOWS){
        active=s;count=0;emit(s,"{\"windows\":[");EnumWindows(top,0);
        GUITHREADINFO info={0};info.cbSize=sizeof(info);GetGUIThreadInfo(GetCurrentThreadId(),&info);
        emit(s,"],\"focus\":%lu,\"flags\":%lu}",(DWORD)(uintptr_t)info.hwndFocus,info.flags);active=NULL;return;
    }
    if(!IsWindowVisible(window)||!IsWindowEnabled(window)){s->error=31;return;}
    if(s->action==FOCUS){
        SetForegroundWindow(GetAncestor(window,GA_ROOT));SetFocus(window);
        emit(s,"{\"focused\":%s}",GetFocus()==window?"true":"false");return;
    }
    WCHAR title[256];GetWindowTextW(GetAncestor(window,GA_ROOT),title,256);
    char cls[80];GetClassNameA(window,cls,80);
    if(wcscmp(title,L"用户登录")){s->error=32;return;}
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
