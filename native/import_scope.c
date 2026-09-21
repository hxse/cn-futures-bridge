/* 仅在本次原生导入调用期间替换选文件与信息提示，随后恢复 IAT。 */
#include "probe.h"
#include <commdlg.h>
#include <string.h>

static ProbeState *active;
static BOOL (WINAPI *original_open)(LPOPENFILENAMEA);
static int (WINAPI *original_message)(HWND,LPCSTR,LPCSTR,UINT);

static BOOL WINAPI provide_file(LPOPENFILENAMEA ofn) {
    if (!active || GetCurrentThreadId()!=active->gui_thread) return original_open(ofn);
    active->open_calls++;
    BYTE *base=(BYTE *)GetModuleHandleW(NULL);
    /* WTL 会先登记待创建窗口。无窗口时也须消费登记，避免留下栈指针。 */
    BYTE *dialog=(BYTE *)ofn-0x20, *entry=dialog+8;
    CRITICAL_SECTION *lock=(void *)(base+0x987d64);
    EnterCriticalSection(lock);
    BOOL matches=*(BYTE **)(base+0x987d7c)==entry
        && *(BYTE **)entry==dialog && *(DWORD *)(entry+4)==GetCurrentThreadId();
    if (matches) {
        typedef void *(WINAPI *ExtractWindowData)(void *);
        ExtractWindowData extract=(ExtractWindowData)(base+0x4540);
        if (extract(base+0x987d60)==dialog) active->atl_consumed++;
        else matches=FALSE;
    }
    LeaveCriticalSection(lock);
    if (!matches) { active->error=12; return FALSE; }
    size_t length=strlen(active->path);
    if (!ofn->lpstrFile || ofn->nMaxFile<=length || active->open_calls!=1) {
        active->error=13; return FALSE;
    }
    memcpy(ofn->lpstrFile,active->path,length+1);
    const char *filename=strrchr(active->path,'\\');
    ofn->nFileOffset=(WORD)(filename ? filename+1-active->path : 0);
    ofn->nFileExtension=(WORD)(length-3);
    return TRUE;
}

static int WINAPI capture_message(HWND window,LPCSTR text,LPCSTR title,UINT flags) {
    if (!active || GetCurrentThreadId()!=active->gui_thread)
        return original_message(window,text,title,flags);
    active->message_calls++; active->message_flags=flags;
    size_t used=strlen(active->message_text);
    if (used && used+1<sizeof(active->message_text)) active->message_text[used++]='\n';
    lstrcpynA(active->message_text+used,text?text:"",sizeof(active->message_text)-used);
    lstrcpynA(active->message_title,title?title:"",sizeof(active->message_title));
    if ((flags&MB_TYPEMASK)!=MB_OK) { active->error=14; return IDCANCEL; }
    return IDOK;
}

static int replace_slot(void **slot,void *replacement,void **previous) {
    DWORD protection,ignored;
    if (!VirtualProtect(slot,sizeof(*slot),PAGE_READWRITE,&protection)) return 0;
    *previous=InterlockedExchangePointer(slot,replacement);
    return VirtualProtect(slot,sizeof(*slot),protection,&ignored)!=0;
}

void import_csv(ProbeState *s) {
    
    size_t length=strlen(s->path);
    s->open_calls=s->message_calls=s->message_flags=s->atl_consumed=0;
    s->restored=s->import_completed=0; s->message_text[0]=s->message_title[0]=0;
    if (length<16 || length>=sizeof(s->path)-1 || !valid_path(s)
        || strstr(s->path,"..") || strcmp(s->path+length-4,".csv")) { s->error=5; return; }
    DWORD attributes=GetFileAttributesA(s->path);
    if (attributes==INVALID_FILE_ATTRIBUTES || (attributes&FILE_ATTRIBUTE_DIRECTORY)) {
        s->error=10; return;
    }
    BYTE *base=(BYTE *)GetModuleHandleW(NULL);
    const BYTE signature[]={0x55,0x8b,0xec,0x6a,0xff,0x68,0xdf,0xc0,0xbc,0x00};
    if (memcmp(base+0x2e61e0,signature,sizeof(signature))) { s->error=1; return; }
    void **open_slot=(void **)(base+0x8190b0), **message_slot=(void **)(base+0x81972c);
    original_open=(void *)*open_slot; original_message=(void *)*message_slot;
    if ((void *)original_open!=(void *)GetProcAddress(GetModuleHandleA("comdlg32.dll"),"GetOpenFileNameA")
        || (void *)original_message!=(void *)GetProcAddress(GetModuleHandleA("user32.dll"),"MessageBoxA")) {
        s->error=11; return;
    }
    void *previous;
    BOOL message_set=replace_slot(message_slot,(void *)capture_message,&previous);
    BOOL open_set=message_set && replace_slot(open_slot,(void *)provide_file,&previous);
    if (message_set && open_set) {
        active=s;
        LARGE_INTEGER before,after,frequency; QueryPerformanceFrequency(&frequency);
        QueryPerformanceCounter(&before);
        typedef void (__attribute__((thiscall)) *ImportFunction)(void *);
        ((ImportFunction)(base+0x2e61e0))((void *)(uintptr_t)s->object);
        QueryPerformanceCounter(&after);
        s->export_ms=(after.QuadPart-before.QuadPart)*1000.0/frequency.QuadPart;
        s->import_completed=1; active=NULL;
    } else s->error=15;
    /* 即使安装阶段失败，也按实际槽位恢复，不能遗留临时拦截。 */
    BOOL open_restored=replace_slot(open_slot,(void *)original_open,&previous);
    BOOL message_restored=replace_slot(message_slot,(void *)original_message,&previous);
    s->restored=open_restored && message_restored
        && *open_slot==(void *)original_open && *message_slot==(void *)original_message;
    if (!s->restored) s->error=16;
}
