/* 实时读取表格绑定事实；不缓存行数据，不选择或发送订单。 */
#include "probe.h"
#include <stdio.h>
#include <string.h>
#include <wchar.h>

static int valid_object(uintptr_t pointer,HWND window){
    BYTE *base=(BYTE *)GetModuleHandleW(NULL);
    if(!readable((void *)pointer,0x4e4))return 0;
    uintptr_t vtable=*(DWORD *)pointer;
    return vtable>=(uintptr_t)base+0x819000&&vtable<(uintptr_t)base+0x953854
        &&*(HWND *)(pointer+4)==window
        &&*(int *)(pointer+0x458)>0&&*(int *)(pointer+0x458)<200;
}

void inspect_grid(ProbeState *s,HWND window){
    s->procedure=(DWORD)GetWindowLongPtrA(window,GWLP_WNDPROC);
    s->userdata=(DWORD)GetWindowLongPtrW(window,GWLP_USERDATA);
    s->object=0;s->columns=s->vtable=s->object_window=0;
    BYTE *p=(BYTE *)(uintptr_t)s->procedure;
    memset(s->procedure_bytes,0,16);
    if(readable(p,16)){
        memcpy(s->procedure_bytes,p,16);
        /* WTL 的 x86 thunk 将窗口参数改写为实例指针。 */
        if(p[0]==0xc7&&p[1]==0x44&&p[2]==0x24&&p[3]==0x04&&p[8]==0xe9){
            DWORD object;memcpy(&object,p+4,4);
            if(valid_object(object,window))s->object=object;
        }
    }
    if(!s->object&&valid_object(s->userdata,window))s->object=s->userdata;
    if(s->object){
        s->vtable=*(DWORD *)(uintptr_t)s->object;
        s->object_window=*(DWORD *)(uintptr_t)(s->object+4);
        s->columns=*(DWORD *)(uintptr_t)(s->object+0x458);
    }
}

void grid_query(ProbeState *s,HWND main){
    unsigned long raw=0;char tail;
    if(sscanf(s->argument,"%lu %c",&raw,&tail)!=1){s->error=32;return;}
    HWND window=(HWND)(uintptr_t)raw;
    DWORD pid=0,thread=GetWindowThreadProcessId(window,&pid);
    WCHAR cls[80];GetClassNameW(window,cls,80);
    s->columns=s->object=0;
    if(!IsWindow(window)||pid!=s->pid||thread!=s->gui_thread||wcscmp(cls,L"ListCtrl")
        ||GetAncestor(window,GA_ROOT)!=main||!matches_main(s,main)){
        emit(s,"{\"valid\":false}");return;
    }
    inspect_grid(s,window);
    if(!s->object){emit(s,"{\"valid\":false}");return;}
    emit(s,"{\"valid\":true,\"object\":%lu,\"procedure\":%lu,\"vtable\":%lu,\"columns\":%lu,\"window\":",
         s->object,s->procedure,s->vtable,s->columns);
    emit_window(s,window);emit(s,",\"filters\":[");
    unsigned count=0,visited=0;
    for(HWND child=GetWindow(GetParent(window),GW_CHILD);child;child=GetWindow(child,GW_HWNDNEXT)){
        if(++visited>512){s->error=30;return;}
        GetClassNameW(child,cls,80);
        if(wcscmp(cls,L"Button"))continue;
        if(count++)emit(s,",");
        emit_window(s,child);
    }
    emit(s,"]}");
}
