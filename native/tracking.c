/* 按实际会话/引用查询既有订单和其成交；不使用不明约束的按请求编号查询函数。 */
#include "probe.h"
#include <stdio.h>
#include <string.h>
#include <math.h>

typedef void *(__cdecl *List)(void);
typedef const void *(__cdecl *Next)(void *);
typedef void (__cdecl *Release)(void *);
typedef long (__cdecl *Number)(const void *);
typedef const char *(__cdecl *Text)(const void *);
typedef double (__cdecl *Amount)(const void *);
typedef const void *(__cdecl *Reference)(const void *);
typedef struct {List get;Next first,next;Release release;} Lists;

static int list_api(HMODULE core,const char *type,Lists *api){
    char name[160];
    snprintf(name,sizeof(name),"?Get%sList@@YAPAU%sList@@XZ",type,type);api->get=(List)(void *)GetProcAddress(core,name);
    snprintf(name,sizeof(name),"?%sList_First@@YAPAU%s@@PAU%sList@@@Z",type,type,type);api->first=(Next)(void *)GetProcAddress(core,name);
    snprintf(name,sizeof(name),"?%sList_Next@@YAPAU%s@@PAU%sList@@@Z",type,type,type);api->next=(Next)(void *)GetProcAddress(core,name);
    snprintf(name,sizeof(name),"?%sList_Release@@YAXPAU%sList@@@Z",type,type);api->release=(Release)(void *)GetProcAddress(core,name);
    return api->get&&api->first&&api->next&&api->release;
}

void tracking_query(ProbeState *s,HWND window){
    if(!matches_main(s,window)){s->error=43;return;}
    long front,session,exchange;char ref[13],instrument[80],extra;
    if(sscanf(s->argument,"%ld %ld %12s %ld %79s %c",&front,&session,ref,&exchange,instrument,&extra)!=5||front<0){s->error=44;return;}
    for(const char *p=ref;*p;p++)if(*p<'0'||*p>'9'){s->error=44;return;}
    HMODULE core=GetModuleHandleA("shinny_future_core.dll");Lists orders,trades;
    if(!core||!list_api(core,"Order",&orders)||!list_api(core,"Trade",&trades)){s->error=40;return;}
    const char *number_names[]={"FrontId","SessionId","ExchangeId","Direction","OffsetFlag","HedgeFlag","VolumeTotal","VolumeTraded","VolumeCurrent"};
    const char *number_keys[]={"front_id","session_id","exchange","side","offset","hedge","volume","filled","remaining"};
    const char *text_names[]={"InsId","OrderRef","OrderSysId","StatusMsg","InsertDateTime"};
    const char *text_keys[]={"instrument_hex","order_ref_hex","order_id_hex","message_hex","time_hex"};
    Number nums[9];Text texts[5];char symbol[160];
    for(int i=0;i<9;i++){
        snprintf(symbol,sizeof(symbol),"?Order_%s@@YAJPBUOrder@@@Z",number_names[i]);
        nums[i]=(Number)(void *)GetProcAddress(core,symbol);if(!nums[i]){s->error=40;return;}
    }
    for(int i=0;i<5;i++){
        snprintf(symbol,sizeof(symbol),"?Order_%s@@YAPBDPBUOrder@@@Z",text_names[i]);
        texts[i]=(Text)(void *)GetProcAddress(core,symbol);if(!texts[i]){s->error=40;return;}
    }
    Amount price=(Amount)(void *)GetProcAddress(core,"?Order_LimitPrice@@YANPBUOrder@@@Z");
    Reference trade_order=(Reference)(void *)GetProcAddress(core,"?Trade_Order@@YAPBUOrder@@PBUTrade@@@Z");
    Text trade_id=(Text)(void *)GetProcAddress(core,"?Trade_TradeId@@YAPBDPBUTrade@@@Z");
    Text trade_sys=(Text)(void *)GetProcAddress(core,"?Trade_OrderSysId@@YAPBDPBUTrade@@@Z");
    if(!price||!trade_order||!trade_id||!trade_sys){s->error=40;return;}
    void *list=orders.get();if(!list){s->error=41;return;}
    int count=0,visited=0;emit(s,"{\"orders\":[");
    for(const void *item=orders.first(list);item;item=orders.next(list)){
        if(++visited>100000){s->error=30;break;}
        if(nums[0](item)!=front||nums[1](item)!=session||nums[2](item)!=exchange||
           strcmp(texts[0](item),instrument)||strcmp(texts[1](item),ref))continue;
        if(count>=2){s->error=30;break;}
        if(count++)emit(s,",");
        double limit=price(item);if(!isfinite(limit)){s->error=44;break;}
        emit(s,"{\"price\":%.17g",limit);
        for(int i=0;i<9;i++)emit(s,",\"%s\":%ld",number_keys[i],nums[i](item));
        for(int i=0;i<5;i++){emit(s,",\"%s\":\"",text_keys[i]);hex_text(s,texts[i](item));emit(s,"\"");}
        emit(s,"}");
    }
    orders.release(list);if(s->error)return;
    emit(s,"],\"trades\":[");visited=count=0;
    list=trades.get();if(!list){s->error=41;return;}
    for(const void *item=trades.first(list);item;item=trades.next(list)){
        if(++visited>100000){s->error=30;break;}
        const void *order=trade_order(item);if(!order)continue;
        if(nums[0](order)!=front||nums[1](order)!=session||nums[2](order)!=exchange||
           strcmp(texts[0](order),instrument)||strcmp(texts[1](order),ref))continue;
        if(count>=2000){s->error=30;break;}
        if(count++)emit(s,",");
        emit(s,"{\"trade_id_hex\":\"");hex_text(s,trade_id(item));
        emit(s,"\",\"order_id_hex\":\"");hex_text(s,trade_sys(item));emit(s,"\"}");
    }
    trades.release(list);emit(s,"]}");
}
