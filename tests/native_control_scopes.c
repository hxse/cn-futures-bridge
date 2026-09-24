/* 独立 Win32 夹具：只验证窗口范围与模拟对象布局，不执行快期私有函数。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static ProbeState state;
static HWND main_window;
static unsigned unrelated_reads;
static WNDPROC combo_procedure;
int readable(const void *pointer,size_t size){
    MEMORY_BASIC_INFORMATION m;
    if(!pointer||!VirtualQuery(pointer,&m,sizeof(m))||m.State!=MEM_COMMIT
        ||(m.Protect&(PAGE_NOACCESS|PAGE_GUARD)))return 0;
    return (uintptr_t)pointer+size>=(uintptr_t)pointer
        &&(uintptr_t)pointer+size<=(uintptr_t)m.BaseAddress+m.RegionSize;
}
/* 文档识别由既有独立夹具覆盖，本夹具不模拟登录或交易通知。 */
int confirm_document(ProbeState *s,HWND w,int runtime){return 0;}
void reset_notice_state(ProbeState *s){}
static void require(int ok,const char *message){
    if(!ok){fprintf(stderr,"FAIL %s: error=%lu %s\n",message,state.error,state.output);exit(1);}
}
static int has(const char *text){return strstr(state.output,text)!=NULL;}
static HWND child(HWND parent,const WCHAR *cls,const WCHAR *text,int id,DWORD extra){
    HWND w=CreateWindowW(cls,text,WS_CHILD|WS_VISIBLE|extra,0,0,100,20,parent,(HMENU)(uintptr_t)id,GetModuleHandleW(NULL),NULL);
    require(w!=NULL,"create child");return w;
}
static HWND panel(void){
    HWND p=child(main_window,L"Static",L"",500,0);
    child(p,L"Button",L"预埋/条件",3324,0);child(p,L"Button",L"下单",3320,0);
    return p;
}
static void query(DWORD action,HWND target){
    state.action=action;state.error=0;state.output[0]=0;
    snprintf(state.argument,sizeof(state.argument),"%lu",(DWORD)(uintptr_t)target);
    if(action==GRID_BINDING)grid_query(&state,main_window);else gui_query(&state,main_window);
    require(!state.error,"native query completed");
}
static LRESULT CALLBACK observe_combo(HWND w,UINT message,WPARAM wp,LPARAM lp){
    if(message==WM_GETTEXT||message==CB_GETCOUNT||message==CB_GETLBTEXT)unrelated_reads++;
    return CallWindowProcW(combo_procedure,w,message,wp,lp);
}
int wmain(void){
    state.pid=GetCurrentProcessId();state.gui_thread=GetCurrentThreadId();
    wcscpy(state.main_titles[0],L"CFB Scope Test");wcscpy(state.main_titles[1],L"CFB Scope Test");
    WNDCLASSW cls={0};cls.lpfnWndProc=DefWindowProcW;cls.hInstance=GetModuleHandleW(NULL);cls.lpszClassName=L"ListCtrl";
    require(RegisterClassW(&cls)!=0,"register ListCtrl fixture");
    main_window=CreateWindowW(L"Static",L"CFB Scope Test",WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                             0,0,640,480,NULL,NULL,cls.hInstance,NULL);
    require(main_window!=NULL,"create main");
    HWND p=panel(),wrapper=child(p,L"Static",L"",3301,0);
    HWND edit=child(wrapper,L"Edit",L"m2701",3301,0);
    HWND combo=child(main_window,L"ComboBox",L"",9000,CBS_DROPDOWNLIST);
    SendMessageW(combo,CB_ADDSTRING,0,(LPARAM)L"UNRELATED");
    combo_procedure=(WNDPROC)SetWindowLongPtrW(combo,GWLP_WNDPROC,(LONG_PTR)observe_combo);
    unrelated_reads=0;
    query(FORM_WINDOWS,NULL);
    require(has("\"valid\":true")&&has("\"id\":3301")&&!has("\"id\":9000"),"scope excludes unrelated fields");
    require(has("6d32373031")&&!unrelated_reads,"form reads current text without unrelated list");
    SetWindowTextW(edit,L"m2705");query(FORM_WINDOWS,p);
    require(has("6d32373035")&&!has("6d32373031")&&!unrelated_reads,"cached panel rereads current value");
    HWND duplicate=panel();query(FORM_WINDOWS,NULL);
    require(has("\"valid\":false"),"duplicate form rejected");DestroyWindow(duplicate);
    ShowWindow(p,SW_HIDE);query(FORM_WINDOWS,p);require(has("\"valid\":false"),"hidden panel invalid");ShowWindow(p,SW_SHOW);
    EnableWindow(p,FALSE);query(FORM_WINDOWS,p);require(has("\"valid\":false"),"disabled panel invalid");EnableWindow(p,TRUE);
    query(FORM_WINDOWS,edit);require(has("\"valid\":false"),"edit cannot masquerade as panel");
    query(WINDOWS,NULL);require(unrelated_reads>0,"full path still reads full window metadata");

    HWND table_parent=child(main_window,L"Static",L"",600,0);
    HWND table=child(table_parent,L"ListCtrl",L"",4201,0);
    HWND filter=child(table_parent,L"Button",L"全部(A)",4202,BS_RADIOBUTTON);
    SendMessageW(filter,BM_SETCHECK,BST_CHECKED,0);
    BYTE *object=HeapAlloc(GetProcessHeap(),HEAP_ZERO_MEMORY,0x4e4);require(object!=NULL,"allocate layout fixture");
    *(DWORD *)object=(DWORD)(uintptr_t)GetModuleHandleW(NULL)+0x819100;
    *(HWND *)(object+4)=table;*(int *)(object+0x458)=12;
    SetWindowLongPtrW(table,GWLP_USERDATA,(LONG_PTR)object);
    query(GRID_BINDING,table);
    require(has("\"valid\":true")&&has("\"columns\":12")&&has("\"id\":4201"),"valid grid object binding");
    const char *filters=strstr(state.output,"\"filters\":[");
    require(filters&&strstr(filters,"\"checked\":1"),"filter checked fact");
    SendMessageW(filter,BM_SETCHECK,BST_UNCHECKED,0);query(GRID_BINDING,table);
    filters=strstr(state.output,"\"filters\":[");require(filters&&strstr(filters,"\"checked\":0"),"filter change reread");
    *(int *)(object+0x458)=9;query(GRID_BINDING,table);require(has("\"columns\":9"),"layout change reread");
    *(HWND *)(object+4)=edit;query(GRID_BINDING,table);require(has("\"valid\":false"),"object window mismatch");
    *(HWND *)(object+4)=table;*(DWORD *)object=0;
    query(GRID_BINDING,table);require(has("\"valid\":false"),"invalid vtable rejected");
    query(GRID_BINDING,p);require(has("\"valid\":false"),"wrong class rejected");
    DestroyWindow(table);query(GRID_BINDING,table);require(has("\"valid\":false"),"destroyed grid rejected");
    HeapFree(GetProcessHeap(),0,object);DestroyWindow(p);
    query(FORM_WINDOWS,p);require(has("\"valid\":false"),"destroyed form rejected");
    DestroyWindow(main_window);
    puts("PASS: scoped form reads, fresh values, expired bindings, object/layout/filter validation");
    return 0;
}
