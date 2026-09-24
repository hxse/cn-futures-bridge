/* 独立 Wine 夹具：真实窗口/菜单调用，不启动快期或连接账户。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static ProbeState state;
static HWND main_window;
static int menu_check,expect_owned;

/* common.c 的私有对象读取不在夹具范围内；就绪链路不调用它。 */
int readable(const void *pointer,size_t size){return 0;}
static void require(int value,const char *message){
    if(!value){fprintf(stderr,"FAIL: %s (native=%lu)\n",message,state.error);exit(1);}
}
static void query(DWORD action,HWND target){
    state.action=action;state.error=0;state.output[0]=0;
    readiness_query(&state,target);
}
static int has(const char *value){return strstr(state.output,value)!=NULL;}
static void pump(void){
    MSG msg;
    while(PeekMessageW(&msg,NULL,0,0,PM_REMOVE)){TranslateMessage(&msg);DispatchMessageW(&msg);}
}
static INT_PTR CALLBACK dialog_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
    if(message==WM_CLOSE){EnableWindow(GetWindow(w,GW_OWNER),TRUE);DestroyWindow(w);return TRUE;}
    return FALSE;
}
static HWND dialog(const WCHAR *title,int body){
    struct {DLGTEMPLATE box;WORD menu,cls,title;} template={0};
    template.box.style=WS_POPUP|WS_CAPTION|WS_SYSMENU|DS_MODALFRAME;
    template.box.x=10;template.box.y=10;template.box.cx=180;template.box.cy=100;
    HWND w=CreateDialogIndirectParamW(GetModuleHandleW(NULL),&template.box,main_window,dialog_proc,0);
    require(w!=NULL,"create dialog");SetWindowTextW(w,title);
    if(body)require(CreateWindowW(L"Static",L"fixture",WS_CHILD|WS_VISIBLE,10,10,100,20,
                                 w,(HMENU)7201,GetModuleHandleW(NULL),NULL)!=NULL,"create funds body");
    ShowWindow(w,SW_SHOW);pump();return w;
}
static LRESULT CALLBACK procedure(HWND w,UINT message,WPARAM wp,LPARAM lp){
    if(message==WM_TIMER){
        KillTimer(w,1);
        query(GUI_STATE,main_window);
        if(!has(expect_owned?"\"menu_owned\":true":"\"menu_owned\":false")){
            GUITHREADINFO info={0};info.cbSize=sizeof(info);GetGUIThreadInfo(GetCurrentThreadId(),&info);
            fprintf(stderr,"menu diagnostic: expected=%d main=%p owner=%p root=%p state=%s\n",
                    expect_owned,main_window,info.hwndMenuOwner,GetAncestor(info.hwndMenuOwner,GA_ROOT),state.output);
            char cls[80];GetClassNameA(info.hwndMenuOwner,cls,80);
            fprintf(stderr,"menu window: class=%s parent=%p owner=%p active=%p capture=%p\n",cls,
                    GetParent(info.hwndMenuOwner),GetWindow(info.hwndMenuOwner,GW_OWNER),info.hwndActive,info.hwndCapture);
        }
        require(!state.error&&has(expect_owned?"\"menu_owned\":true":"\"menu_owned\":false"),"menu ownership");
        query(GUI_RECOVER,main_window);
        if(expect_owned)require(!state.error&&has("\"action\":\"menu\""),"cancel owned menu");
        else {require(state.error==72,"reject foreign menu");EndMenu();}
        menu_check++;return 0;
    }
    return DefWindowProcW(w,message,wp,lp);
}
static void check_menu(HWND owner,int owned){
    expect_owned=owned;
    HMENU menu=CreatePopupMenu();require(menu!=NULL,"create menu");
    AppendMenuW(menu,MF_STRING,100,L"fixture action");
    SetForegroundWindow(owner);SetFocus(owner);
    SetTimer(owner,1,30,NULL);
    TrackPopupMenu(menu,TPM_RETURNCMD,40,40,0,owner,NULL);
    KillTimer(owner,1);DestroyMenu(menu);pump();
    query(GUI_STATE,main_window);require(!state.error&&has("\"flags\":0"),"menu exit confirmed");
}
int wmain(void){
    state.pid=GetCurrentProcessId();state.gui_thread=GetCurrentThreadId();
    wcscpy(state.main_titles[0],L"CFB Gui Test");wcscpy(state.main_titles[1],L"CFB Gui Test");
    WNDCLASSW cls={0};cls.lpfnWndProc=procedure;cls.hInstance=GetModuleHandleW(NULL);cls.lpszClassName=L"CfbFixture";
    require(RegisterClassW(&cls)!=0,"register fixture class");
    main_window=CreateWindowW(cls.lpszClassName,L"CFB Gui Test",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                             0,0,640,480,NULL,NULL,cls.hInstance,NULL);
    require(main_window!=NULL,"create main");pump();
    query(GUI_STATE,main_window);require(!state.error&&has("\"main_count\":1")&&has("\"dialogs\":0"),"clean state");
    HWND w=dialog(L"",0);
    query(GUI_STATE,main_window);require(!state.error&&has("\"unknown_dialogs\":1")&&has("\"enabled\":true"),"empty title while main enabled");
    query(GUI_RECOVER,w);require(state.error==72&&IsWindowVisible(w),"do not close empty title");DestroyWindow(w);
    w=dialog(L"期货资金账户详情",0);
    query(GUI_RECOVER,w);require(state.error==72&&IsWindowVisible(w),"same title without body rejected");DestroyWindow(w);
    w=dialog(L"确认下单",1);
    query(GUI_RECOVER,w);require(state.error==72&&IsWindowVisible(w),"trading confirmation rejected");DestroyWindow(w);
    w=dialog(L"期货资金账户详情",1);EnableWindow(main_window,FALSE);
    query(GUI_STATE,main_window);require(!state.error&&has("\"funds_count\":1")&&has("\"unknown_dialogs\":0"),"funds template");
    query(GUI_RECOVER,w);require(!state.error&&IsWindow(w),"funds close posted asynchronously");
    pump();require(!IsWindow(w)&&IsWindowEnabled(main_window),"funds closed and main enabled");
    check_menu(main_window,1);
    HWND other=CreateWindowW(cls.lpszClassName,L"Foreign",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                            0,0,300,200,NULL,NULL,cls.hInstance,NULL);
    require(other!=NULL,"create foreign owner");check_menu(other,0);DestroyWindow(other);
    require(menu_check==2,"both menu callbacks ran");
    LARGE_INTEGER a,b,f;QueryPerformanceFrequency(&f);QueryPerformanceCounter(&a);
    for(int i=0;i<100;i++){query(GUI_STATE,main_window);require(!state.error&&has("\"dialogs\":0"),"repeat clean check");}
    QueryPerformanceCounter(&b);
    printf("PASS: native GUI readiness; healthy mean %.3f ms (100 samples, fixture only)\n",(b.QuadPart-a.QuadPart)*1000.0/f.QuadPart/100);
    DestroyWindow(main_window);return 0;
}
