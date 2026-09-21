/* 原生输出只回传结构化事实，禁止把外部字符串当作代码或任意函数地址。 */
#include "probe.h"
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <wchar.h>

int matches_main(ProbeState *s,HWND window){
    WCHAR title[512];GetWindowTextW(GetAncestor(window,GA_ROOT),title,512);
    for(int i=0;i<2;i++){
        size_t n=wcslen(s->main_titles[i]),m=wcslen(title);
        if(n&&m>=n&&!wcscmp(title+m-n,s->main_titles[i]))return 1;
    }
    return 0;
}

void emit(ProbeState *s,const char *format,...) {
    size_t used=strlen(s->output),space=sizeof(s->output)-used;
    if(space<=1){s->error=30;return;}
    va_list args;va_start(args,format);
    int n=vsnprintf(s->output+used,space,format,args);va_end(args);
    if(n<0 || (size_t)n>=space)s->error=30;
}
void hex_text(ProbeState *s,const char *value) {
    if(!value)return;
    for(size_t i=0;i<16384 && value[i];i++)emit(s,"%02x",(unsigned char)value[i]);
}
uintptr_t window_object(HWND window,int dialog) {
    BYTE *p=(BYTE *)GetWindowLongPtrA(window,dialog?DWLP_DLGPROC:GWLP_WNDPROC);
    if(!readable(p,16)||p[0]!=0xc7||p[1]!=0x44||p[2]!=0x24||p[3]!=4||p[8]!=0xe9)return 0;
    DWORD object;memcpy(&object,p+4,4);
    if(!readable((void *)(uintptr_t)object,0x3b0)||*(HWND *)(uintptr_t)(object+4)!=window)return 0;
    uintptr_t base=(uintptr_t)GetModuleHandleW(NULL),vtable=*(DWORD *)(uintptr_t)object;
    if(vtable<base+0x819000 || vtable>=base+0x953854)return 0;
    return object;
}
int valid_path(ProbeState *s) {
    size_t root=strlen(s->root),n=strlen(s->path);
    return root>3 && n>root+8 && n<sizeof(s->path)-1 && !strncmp(s->path,s->root,root)
        && !strncmp(s->path+root,"op-",3) && !strstr(s->path,"..")
        && !strcmp(s->path+n-4,".csv");
}
