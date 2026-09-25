/* 网页窗口按归属、控件和导航地址识别；不等待页面渲染或读取网页账户内容。 */
#define COBJMACROS
#include "probe.h"
#include <exdisp.h>
#include <wchar.h>

static int monitor_url(const WCHAR *url,UINT length){
    const WCHAR *origin=L"https://investorservice.cfmmc.com";
    size_t origin_length=wcslen(origin);
    if(length<=origin_length||length>8192)return 0;
    for(UINT i=0;i<length;i++)if(url[i]<32||url[i]==127)return 0;
    if(_wcsnicmp(url,origin,origin_length))return 0;
    const WCHAR *path=url+origin_length;
    if(!wcsncmp(path,L":443/",5))path+=4;
    if(*path!=L'/')return 0;
    size_t path_length=wcscspn(path,L"?#");
    return path_length==1||(path_length==14&&!wcsncmp(path,L"/loginByKey.do",14));
}

int monitor_window(ProbeState *s,HWND window){
    DWORD pid=0,thread=GetWindowThreadProcessId(window,&pid);
    WCHAR cls[64]={0};GetClassNameW(window,cls,64);
    if(pid!=s->pid||thread!=s->gui_thread||GetAncestor(window,GA_ROOT)!=window
        ||wcscmp(cls,L"#32770")||!IsWindowVisible(window)
        ||!(GetWindowLongW(window,GWL_STYLE)&WS_SYSMENU)
        ||GetDlgItem(window,IDOK)||GetDlgItem(window,IDCANCEL))return MONITOR_NONE;
    HWND owner=GetWindow(window,GW_OWNER);
    thread=GetWindowThreadProcessId(owner,&pid);
    if(!owner||pid!=s->pid||thread!=s->gui_thread||!matches_main(s,owner))return MONITOR_NONE;
    HWND host=GetWindow(window,GW_CHILD);
    thread=GetWindowThreadProcessId(host,&pid);GetClassNameW(host,cls,64);
    if(!host||GetWindow(host,GW_HWNDNEXT)||GetParent(host)!=window
        ||GetDlgCtrlID(host)!=1224||wcscmp(cls,L"AtlAxWinLic100")
        ||!IsWindowVisible(host)||pid!=s->pid||thread!=s->gui_thread)return MONITOR_NONE;
    /* 只能在宿主 GUI 线程使用 ATL 返回的进程内 COM 引用。 */
    if(GetCurrentThreadId()!=s->gui_thread)return MONITOR_NONE;
    UINT message=RegisterWindowMessageW(L"WM_ATLGETCONTROL");
    if(!message)return MONITOR_PENDING;
    IUnknown *unknown=(IUnknown *)SendMessageW(host,message,0,0);
    if(!unknown)return MONITOR_PENDING;
    IWebBrowser2 *browser=NULL;
    HRESULT result=IUnknown_QueryInterface(unknown,&IID_IWebBrowser2,(void **)&browser);
    IUnknown_Release(unknown);
    if(FAILED(result)||!browser)return MONITOR_PENDING;
    BSTR url=NULL;
    result=IWebBrowser2_get_LocationURL(browser,&url);
    IWebBrowser2_Release(browser);
    int state=MONITOR_PENDING;
    if(SUCCEEDED(result)&&url&&SysStringLen(url))
        state=monitor_url(url,SysStringLen(url))?MONITOR_READY:MONITOR_NONE;
    SysFreeString(url);
    return state;
}
