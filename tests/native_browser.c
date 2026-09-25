/* 独立 Wine 夹具：真实窗口归属，在浏览器 COM 边界提供固定导航地址。 */
#define COBJMACROS
#include "probe.h"
#include <exdisp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static ProbeState state;
static HWND main_window;
static const WCHAR *location;
static UINT location_length,control_message,queries,closes;
static ULONG references=1;
static IWebBrowser2 browser;

int readable(const void *pointer,size_t size){return 0;}
static void require(int value,const char *message){
    if(!value){fprintf(stderr,"FAIL: %s\n",message);exit(1);}
}
static ULONG STDMETHODCALLTYPE add_ref(IWebBrowser2 *self){return ++references;}
static ULONG STDMETHODCALLTYPE release(IWebBrowser2 *self){return --references;}
static HRESULT STDMETHODCALLTYPE query_interface(IWebBrowser2 *self,REFIID iid,void **output){
    *output=NULL;
    if(!IsEqualIID(iid,&IID_IWebBrowser2))return E_NOINTERFACE;
    *output=self;add_ref(self);return S_OK;
}
static HRESULT STDMETHODCALLTYPE get_location(IWebBrowser2 *self,BSTR *output){
    queries++;*output=location?SysAllocStringLen(location,location_length):NULL;return S_OK;
}
static IWebBrowser2Vtbl browser_vtable={
    .QueryInterface=query_interface,.AddRef=add_ref,.Release=release,.get_LocationURL=get_location
};
static void navigate(const WCHAR *url){location=url;location_length=url?(UINT)wcslen(url):0;}
static LRESULT CALLBACK host_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
    if(message==control_message){add_ref(&browser);return (LRESULT)&browser;}
    return DefWindowProcW(w,message,wp,lp);
}
static INT_PTR CALLBACK dialog_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
    if(message==WM_CLOSE){closes++;ShowWindow(w,SW_HIDE);return TRUE;}
    return FALSE;
}
static void pump(void){
    MSG msg;
    while(PeekMessageW(&msg,NULL,0,0,PM_REMOVE)){TranslateMessage(&msg);DispatchMessageW(&msg);}
}
static HWND dialog(HWND owner,const WCHAR *title,const WCHAR *host_class){
    memset(&state,0,sizeof(state));state.pid=GetCurrentProcessId();state.gui_thread=GetCurrentThreadId();
    wcscpy(state.main_titles[0],L"CFB Browser Test");
    struct {DLGTEMPLATE box;WORD menu,cls,title;} template={0};
    template.box.style=WS_POPUP|WS_CAPTION|WS_SYSMENU|DS_MODALFRAME;
    template.box.cx=200;template.box.cy=100;
    HWND w=CreateDialogIndirectParamW(GetModuleHandleW(NULL),&template.box,owner,dialog_proc,0);
    require(w!=NULL,"create dialog");SetWindowTextW(w,title);
    require(CreateWindowW(host_class,L"",WS_CHILD|WS_VISIBLE,0,0,150,80,w,
                         (HMENU)1224,GetModuleHandleW(NULL),NULL)!=NULL,"create host");
    ShowWindow(w,SW_SHOW);pump();return w;
}
static void rejected(HWND w,const char *message){
    unsigned before=closes;
    require(confirm_document(&state,w,1)==0,message);pump();
    require(IsWindowVisible(w)&&closes==before&&!state.information_close_count,message);
    require(references==1,"COM references released");
}
int wmain(void){
    browser.lpVtbl=&browser_vtable;
    control_message=RegisterWindowMessageW(L"WM_ATLGETCONTROL");
    WNDCLASSW cls={0};cls.lpfnWndProc=host_proc;cls.hInstance=GetModuleHandleW(NULL);cls.lpszClassName=L"AtlAxWinLic100";
    require(RegisterClassW(&cls)!=0,"register host");
    cls.lpfnWndProc=DefWindowProcW;cls.lpszClassName=L"CFBBrowserFixture";
    require(RegisterClassW(&cls)!=0,"register main");
    main_window=CreateWindowW(cls.lpszClassName,L"CFB Browser Test",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                             0,0,640,480,NULL,NULL,cls.hInstance,NULL);
    require(main_window!=NULL,"create main");
    const WCHAR *valid[]={L"https://investorservice.cfmmc.com/",
        L"https://investorservice.cfmmc.com/loginByKey.do?key=offline#part",
        L"HTTPS://INVESTORSERVICE.CFMMC.COM:443/?offline=1"};
    for(unsigned i=0;i<sizeof(valid)/sizeof(valid[0]);i++){
        navigate(valid[i]);HWND w=dialog(main_window,i?L"changed caption":L"",L"AtlAxWinLic100");
        unsigned before=closes;
        require(confirm_document(&state,w,0)==0&&!state.information_close_count,"ordinary scan does not close");
        require(confirm_document(&state,w,1)==1&&state.information_close_count==1,"known origin accepted");
        require(confirm_document(&state,w,1)==1&&state.information_close_count==1,"one async close only");
        require(IsWindowVisible(w)&&closes==before,"posted asynchronously");pump();
        require(!IsWindowVisible(w)&&closes==before+1,"close observed");
        reset_notice_state(&state);ShowWindow(w,SW_SHOW);
        require(confirm_document(&state,w,1)==1&&state.information_close_count==2,"reused window rechecked");
        pump();require(!IsWindowVisible(w)&&references==1,"reuse closed and references released");DestroyWindow(w);
    }
    const WCHAR *invalid[]={L"http://investorservice.cfmmc.com/",L"https://investorservice.cfmmc.com.evil/",
        L"https://investorservice.cfmmc.com@evil/",L"https://user@investorservice.cfmmc.com/",
        L"https://investorservice.cfmmc.com:444/",L"https://investorservice.cfmmc.com/other",
        L"https://investorservice.cfmmc.com/loginByKey.do/",L"https://investorservice.cfmmc.com/\n",
        L"about:blank"};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++){
        navigate(invalid[i]);HWND w=dialog(main_window,L"保证金监控中心",L"AtlAxWinLic100");
        rejected(w,"unapproved navigation does not close, even with exact caption");DestroyWindow(w);
    }
    HWND w=dialog(main_window,L"",L"AtlAxWinLic100");navigate(NULL);
    require(confirm_document(&state,w,1)==1&&!state.information_close_count,"missing address waits");
    navigate(valid[0]);require(confirm_document(&state,w,1)==1&&state.information_close_count==1,"address becomes ready");
    pump();DestroyWindow(w);
    WCHAR embedded[]=L"https://investorservice.cfmmc.com/\0untrusted";
    w=dialog(main_window,L"",L"AtlAxWinLic100");location=embedded;location_length=sizeof(embedded)/sizeof(WCHAR)-1;
    rejected(w,"embedded NUL rejected");DestroyWindow(w);
    navigate(valid[0]);w=dialog(main_window,L"",L"Static");unsigned before=queries;
    rejected(w,"wrong control class rejected");require(queries==before,"wrong class avoids COM");DestroyWindow(w);
    w=dialog(main_window,L"",L"AtlAxWinLic100");
    HWND button=CreateWindowW(L"Button",L"确定",WS_CHILD|WS_VISIBLE,0,0,50,20,w,(HMENU)IDOK,cls.hInstance,NULL);
    require(button!=NULL,"create extra button");rejected(w,"confirmation dialog rejected");DestroyWindow(button);
    HWND extra=CreateWindowW(L"Static",L"extra",WS_CHILD|WS_VISIBLE,0,0,50,20,w,(HMENU)1234,cls.hInstance,NULL);
    require(extra!=NULL,"create extra child");rejected(w,"extra direct child rejected");DestroyWindow(extra);
    state.gui_thread++;rejected(w,"wrong GUI thread rejected");DestroyWindow(w);
    HWND other=CreateWindowW(cls.lpszClassName,L"Foreign",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                            0,0,640,480,NULL,NULL,cls.hInstance,NULL);
    require(other!=NULL,"create other owner");w=dialog(other,L"",L"AtlAxWinLic100");
    rejected(w,"wrong owner rejected");DestroyWindow(w);DestroyWindow(other);DestroyWindow(main_window);
    require(references==1,"all COM references released");
    puts("PASS: browser origin, empty caption, ownership, pending state, close observation and deduplication");
    return 0;
}
