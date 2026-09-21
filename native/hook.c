/* 固定版本原生适配；所有调用归属同一 GUI 线程和有界执行权。 */
#include "probe.h"
#include <string.h>
#include <stdlib.h>
#include <wchar.h>

static HMODULE self_module;
static HHOOK cbt_hook;
static HANDLE mapping;
static ProbeState *state;
static UINT probe_message;

int readable(const void *pointer, size_t size) {
    MEMORY_BASIC_INFORMATION m;
    if (!pointer || !VirtualQuery(pointer,&m,sizeof(m)) || m.State!=MEM_COMMIT
        || (m.Protect&(PAGE_NOACCESS|PAGE_GUARD))) return 0;
    return (uintptr_t)pointer+size >= (uintptr_t)pointer
        && (uintptr_t)pointer+size <= (uintptr_t)m.BaseAddress+m.RegionSize;
}

static LRESULT CALLBACK ObserveDialogs(int code, WPARAM w, LPARAM l) {
    if (code==HCBT_CREATEWND && state) {
        WCHAR name[64];
        GetClassNameW((HWND)w,name,64);
        if (!wcscmp(name,L"#32770")) InterlockedIncrement((LONG *)&state->created_dialogs);
    }
    return CallNextHookEx(cbt_hook,code,w,l);
}

static int compatible(void) {
    BYTE *base=(BYTE *)GetModuleHandleW(NULL);
    IMAGE_DOS_HEADER *dos=(IMAGE_DOS_HEADER *)base;
    IMAGE_NT_HEADERS32 *nt=(IMAGE_NT_HEADERS32 *)(base+dos->e_lfanew);
    if (dos->e_magic!=IMAGE_DOS_SIGNATURE || nt->Signature!=IMAGE_NT_SIGNATURE
        || nt->FileHeader.Machine!=IMAGE_FILE_MACHINE_I386
        || nt->OptionalHeader.SizeOfImage!=0x00a5c000) return 0;
    BYTE *fn=base+0x003b1ba0;
    return fn[0]==0x6a && fn[1]==0xff && fn[2]==0x68
        && fn[7]==0x64 && fn[8]==0xa1 && fn[13]==0x50
        && fn[14]==0x81 && fn[15]==0xec && fn[16]==0x48 && fn[17]==0x03;
}

static int valid_object(uintptr_t pointer, HWND window) {
    BYTE *base=(BYTE *)GetModuleHandleW(NULL);
    if (!readable((void *)pointer,0x4e4)) return 0;
    uintptr_t vtable=*(DWORD *)pointer;
    return vtable>=(uintptr_t)base+0x819000 && vtable<(uintptr_t)base+0x953854
        && *(HWND *)(pointer+4)==window
        && *(int *)(pointer+0x458)>0 && *(int *)(pointer+0x458)<200;
}

static void inspect(HWND window) {
    state->procedure=(DWORD)GetWindowLongPtrA(window,GWLP_WNDPROC);
    state->userdata=(DWORD)GetWindowLongPtrW(window,GWLP_USERDATA);
    state->object=0;
    state->columns=state->vtable=state->object_window=0;
    BYTE *p=(BYTE *)(uintptr_t)state->procedure;
    memset(state->procedure_bytes,0,16);
    if (readable(p,16)) {
        memcpy(state->procedure_bytes,p,16);
        /* WTL 的 x86 thunk 将窗口参数改写为实例指针。 */
        if (p[0]==0xc7 && p[1]==0x44 && p[2]==0x24 && p[3]==0x04 && p[8]==0xe9) {
            DWORD object; memcpy(&object,p+4,4);
            if (valid_object(object,window)) state->object=object;
        }
    }
    if (!state->object && valid_object(state->userdata,window)) state->object=state->userdata;
    if (state->object) {
        state->vtable=*(DWORD *)(uintptr_t)state->object;
        state->object_window=*(DWORD *)(uintptr_t)(state->object+4);
        state->columns=*(DWORD *)(uintptr_t)(state->object+0x458);
    }
}

static void handle(HWND window) {
    if (!state) {
        mapping=OpenFileMappingW(FILE_MAP_ALL_ACCESS,FALSE,PROBE_MAP);
        if (!mapping) return;
        state=MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(*state));
        if (!state) { CloseHandle(mapping); mapping=NULL; return; }
    }
    if (state->magic!=PROBE_MAGIC || state->pid!=GetCurrentProcessId()
        || state->target!=(DWORD)(uintptr_t)window) return;
    state->error=0; state->win_error=0; state->gui_thread=GetCurrentThreadId();
    if (state->action==STOP) {
        if (cbt_hook) { UnhookWindowsHookEx(cbt_hook); cbt_hook=NULL; }
        state->done=1;
        UnmapViewOfFile(state); state=NULL;
        CloseHandle(mapping); mapping=NULL;
        return;
    }
    if (!compatible()) { state->error=1; state->done=1; return; }
    if (!cbt_hook) {
        cbt_hook=SetWindowsHookExW(WH_CBT,ObserveDialogs,self_module,GetCurrentThreadId());
        if (!cbt_hook) { state->error=2; state->win_error=GetLastError(); state->done=1; return; }
    }
    if (state->action==ARM) state->created_dialogs=0;
    if (state->action==INSPECT || state->action==EXPORT || state->action==IMPORT || state->action==SELECT_ROW) {
        WCHAR cls[80], title[256];
        GetClassNameW(window,cls,80);
        GetWindowTextW(GetAncestor(window,GA_ROOT),title,256);
        if (wcscmp(cls,L"ListCtrl") || !wcsstr(title,L"快期2-CTP-上期技术-")) state->error=3;
        else inspect(window);
    }
    if (!state->error && state->action==EXPORT) {
        size_t length=strlen(state->path);
        
        if (!state->object) state->error=4;
        else if (length<16 || length>=sizeof(state->path)-1 || !valid_path(state)
                 || strstr(state->path,"..") || strcmp(state->path+length-4,".csv")) state->error=5;
        else if (GetFileAttributesA(state->path)!=INVALID_FILE_ATTRIBUTES) state->error=6;
        else {
            /* 参数是 MSVC 24 字节 string 的只读引用，快期内部会复制它。 */
            struct { union { char local[16]; const char *pointer; } storage; DWORD length,capacity; } value;
            memset(&value,0,sizeof(value)); value.storage.pointer=state->path;
            value.length=(DWORD)length; value.capacity=(DWORD)length;
            typedef unsigned char (__attribute__((thiscall)) *ExportFunction)(void *,const void *);
            ExportFunction function=(ExportFunction)((BYTE *)GetModuleHandleW(NULL)+0x003b1ba0);
            LARGE_INTEGER before,after,frequency; QueryPerformanceFrequency(&frequency);
            state->created_dialogs=0; QueryPerformanceCounter(&before);
            state->export_result=function((void *)(uintptr_t)state->object,&value);
            QueryPerformanceCounter(&after);
            state->export_ms=(after.QuadPart-before.QuadPart)*1000.0/frequency.QuadPart;
        }
    }
    if (!state->error && state->action==IMPORT) {
        state->created_dialogs=0;
        if (!state->object || GetDlgCtrlID(window)!=4201 || state->columns!=12) state->error=4;
        else import_csv(state);
    }
    if (!state->error && state->action==SELECT_ROW) {
        if (!state->object) state->error=4;
        else {
            typedef int (__attribute__((thiscall)) *Count)(void *);
            typedef BOOL (__attribute__((thiscall)) *Select)(void *,int,int,unsigned);
            void *object=(void *)(uintptr_t)state->object;
            Count count=(Count)(*(DWORD *)(uintptr_t)(state->vtable+0x24));
            state->row_count=count(object);
            int row=atoi(state->argument);
            if (row<0 || row>=state->row_count) state->error=21;
            else {
                SetFocus(window);
                ((Select)((BYTE *)GetModuleHandleW(NULL)+0x8a220))(object,row,-1,0);
                state->selected_count=*(int *)(uintptr_t)(state->object+0x468);
                DWORD head=*(DWORD *)(uintptr_t)(state->object+0x464);
                if (state->selected_count==1 && readable((void *)(uintptr_t)head,4)) {
                    DWORD node=*(DWORD *)(uintptr_t)head;
                    if (readable((void *)(uintptr_t)node,16)) state->selected=*(int *)(uintptr_t)(node+12);
                    else state->error=22;
                } else state->error=22;
                if (state->selected!=row || GetFocus()!=window) state->error=22;
            }
        }
    }
    if (!state->error && state->action>=SESSION && state->action<=INSTRUMENT) native_query(state,window);
    if (!state->error && state->action>=WINDOWS) gui_query(state,window);
    state->done=1;
}

__declspec(dllexport) LRESULT CALLBACK CfbHook(int code, WPARAM w, LPARAM l) {
    if (code>=0) {
        CWPSTRUCT *message=(CWPSTRUCT *)l;
        if (!probe_message) probe_message=RegisterWindowMessageW(PROBE_MESSAGE);
        if (message->message==probe_message) handle(message->hwnd);
    }
    return CallNextHookEx(NULL,code,w,l);
}

BOOL WINAPI DllMain(HINSTANCE module,DWORD reason,LPVOID reserved) {
    if (reason==DLL_PROCESS_ATTACH) { self_module=module; DisableThreadLibraryCalls(module); }
    if (reason==DLL_PROCESS_DETACH) {
        if (state) UnmapViewOfFile(state);
        if (mapping) CloseHandle(mapping);
    }
    return TRUE;
}
