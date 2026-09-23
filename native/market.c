/* 限价 IOC 只读核对：预埋条件与菜单文字，不调用任何内部报单函数。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <math.h>

static void menu_read(ProbeState *s,HWND window){
    WCHAR cls[64];GetClassNameW(window,cls,64);
    DWORD pid,thread=GetWindowThreadProcessId(window,&pid);
    if(wcscmp(cls,L"#32768")||!IsWindowVisible(window)||pid!=s->pid||thread!=s->gui_thread){s->error=43;return;}
    HMENU menu=(HMENU)SendMessageW(window,0x01e1,0,0);
    int count=GetMenuItemCount(menu);
    if(count<1||count>32){s->error=44;return;}
    emit(s,"{\"items\":[");
    for(int i=0;i<count;i++){
        char text[256];int length=GetMenuStringA(menu,i,text,sizeof(text),MF_BYPOSITION);
        if(length<1||length>=255){s->error=44;return;}
        if(i)emit(s,",");
        emit(s,"{\"index\":%d,\"command_id\":%u,\"enabled\":%s,\"text_hex\":\"",i,GetMenuItemID(menu,i),
            GetMenuState(menu,i,MF_BYPOSITION)&(MF_DISABLED|MF_GRAYED)?"false":"true");
        hex_text(s,text);emit(s,"\"}");
    }
    emit(s,"]}");
}

void market_query(ProbeState *s,HWND window){
    if(s->action==MENU){menu_read(s,window);return;}
    if(!matches_main(s,window)){s->error=43;return;}
    char *end;long after=strtol(s->argument,&end,10);
    if(!s->argument[0]||*end||after < -1){s->error=44;return;}
    HMODULE core=GetModuleHandleA("shinny_future_core.dll");
    if(!core){s->error=40;return;}
    typedef void *(__cdecl *List)(void);
    typedef const void *(__cdecl *Next)(void *);
    typedef void (__cdecl *Release)(void *);
    typedef long (__cdecl *Number)(const void *);
    typedef const char *(__cdecl *Text)(const void *);
    typedef double (__cdecl *Amount)(const void *);
    List get=(List)(void *)GetProcAddress(core,"?GetParkedOrderList@@YAPAUParkedOrderList@@XZ");
    Next first=(Next)(void *)GetProcAddress(core,"?ParkedOrderList_First@@YAPAUParkedOrder@@PAUParkedOrderList@@@Z");
    Next next=(Next)(void *)GetProcAddress(core,"?ParkedOrderList_Next@@YAPAUParkedOrder@@PAUParkedOrderList@@@Z");
    Release release=(Release)(void *)GetProcAddress(core,"?ParkedOrderList_Release@@YAXPAUParkedOrderList@@@Z");
    Text instrument=(Text)(void *)GetProcAddress(core,"?ParkedOrder_InsId@@YAPBDPBUParkedOrder@@@Z");
    Amount price=(Amount)(void *)GetProcAddress(core,"?ParkedOrder_LimitPrice@@YANPBUParkedOrder@@@Z");
    const char *names[]={"Id","Direction","OffsetFlag","HedgeFlag","VolumeTotal","OrderPriceSelector","TimeCondition","VolumeCondition"};
    const char *keys[]={"id","side","offset","hedge","volume","selector","time_condition","volume_condition"};
    Number fields[8];char name[160];
    for(int i=0;i<8;i++){
        snprintf(name,sizeof(name),"?ParkedOrder_%s@@YAJPBUParkedOrder@@@Z",names[i]);
        fields[i]=(Number)(void *)GetProcAddress(core,name);
        if(!fields[i]){s->error=40;return;}
    }
    if(!get||!first||!next||!release||!instrument||!price){s->error=40;return;}
    void *list=get();if(!list){s->error=41;return;}
    long last=0;int emitted=0,visited=0;
    emit(s,"{\"items\":[");
    for(const void *item=first(list);item;item=next(list)){
        if(++visited>100000){s->error=30;break;}
        long id=fields[0](item);
        if(id<=0){s->error=44;break;}
        if(id>last)last=id;
        /* -1 只取游标；后续只返回本次填参之后创建的记录，不复制历史交易。 */
        if(after<0||id<=after)continue;
        if(emitted>=16){s->error=30;break;}
        if(emitted++)emit(s,",");
        emit(s,"{\"instrument_hex\":\"");hex_text(s,instrument(item));emit(s,"\"");
        for(int i=0;i<8;i++)emit(s,",\"%s\":%ld",keys[i],fields[i](item));
        double limit=price(item);if(!isfinite(limit)){s->error=44;break;}
        emit(s,",\"price\":%.17g",limit);
        emit(s,"}");
    }
    emit(s,"],\"last_id\":%ld}",last);release(list);
}
