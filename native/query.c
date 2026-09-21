/* 只读取身份、连接、合约资料与交易状态，不包含内部资金或报单函数。 */
#include "probe.h"
#include <string.h>
#include <math.h>
#include <stdio.h>

static HWND status_window;
static BOOL CALLBACK find_status(HWND w,LPARAM unused) {
    if(GetDlgCtrlID(w)==1255 && GetDlgItem(GetParent(w),1256)){status_window=GetParent(w);return FALSE;}
    return TRUE;
}
static const char *small_string(uintptr_t address) {
    if(!readable((void *)address,24))return NULL;
    DWORD length=*(DWORD *)(address+16),capacity=*(DWORD *)(address+20);
    const char *value=capacity<16?(const char *)address:*(const char **)address;
    if(length>240 || capacity<length || !readable(value,length+1) || value[length])return NULL;
    return value;
}
static double optional_price(double value){return isfinite(value)&&fabs(value)<1e20?value:0;}

static int has_metadata(HMODULE core,int product){
    typedef void *(__cdecl *List)(void);
    typedef const void *(__cdecl *First)(void *);
    typedef void (__cdecl *Release)(void *);
    List list=(List)(void *)GetProcAddress(core,product?"?GetProductList@@YAPAUProductList@@XZ":"?GetInstrumentList@@YAPAUInstrumentList@@XZ");
    First first=(First)(void *)GetProcAddress(core,product?"?ProductList_First@@YAPBUProduct@@PAUProductList@@@Z":"?InstrumentList_First@@YAPBUInstrument@@PAUInstrumentList@@@Z");
    Release release=(Release)(void *)GetProcAddress(core,product?"?ProductList_Release@@YAXPAUProductList@@@Z":"?InstrumentList_Release@@YAXPAUInstrumentList@@@Z");
    if(!list||!first||!release)return -1;
    void *items=list();if(!items)return 0;
    int ready=first(items)!=NULL;release(items);return ready;
}

void native_query(ProbeState *s,HWND window) {
    if(!matches_main(s,window)){s->error=43;return;}
    HMODULE core=GetModuleHandleA("shinny_future_core.dll");
    if(!core){s->error=40;return;}
    typedef const void *(__cdecl *Find)(const char *);
    typedef const char *(__cdecl *Text)(const void *);
    typedef long (__cdecl *Number)(const void *);
    typedef double (__cdecl *Amount)(const void *);
    if(s->action==SESSION){
        status_window=NULL;EnumChildWindows(GetAncestor(window,GA_ROOT),find_status,0);
        uintptr_t object=status_window?window_object(status_window,1):0;
        const char *td=object?small_string(object+0x320):NULL;
        const char *md=object?small_string(object+0x33c):NULL;
        /* 使用绘制连接指示时比较的原始文字，不采集颜色或像素。 */
        BYTE *base=(BYTE *)GetModuleHandleW(NULL);
        int trade=td && !strcmp(td,(const char *)(base+0x833a60));
        int market=md && !strcmp(md,(const char *)(base+0x833a54));
        Find get=(Find)(void *)GetProcAddress(core,"?GetUser@@YAPAUUser@@PBD@Z");
        Text id=(Text)(void *)GetProcAddress(core,"?User_Id@@YAPBDPBUUser@@@Z");
        Text day=(Text)(void *)GetProcAddress(core,"?User_Tradingday@@YAPBDPBUUser@@@Z");
        Number front=(Number)(void *)GetProcAddress(core,"?User_FrontId@@YAJPBUUser@@@Z");
        Number session=(Number)(void *)GetProcAddress(core,"?User_SessionId@@YAJPBUUser@@@Z");
        if(!get||!id||!day||!front||!session){s->error=40;return;}
        const void *user=get(s->argument);
        emit(s,"{\"connected\":%s,\"trade_connected\":%s,\"market_connected\":%s,\"identity_match\":%s,\"trading_day\":\"",
            trade&&market?"true":"false",trade?"true":"false",market?"true":"false",user&&id(user)&&!strcmp(id(user),s->argument)?"true":"false");
        const char *date=user?day(user):"";if(!date)date="";
        int valid=strlen(date)==8;for(int i=0;valid&&i<8;i++)if(date[i]<'0'||date[i]>'9')valid=0;
        if(valid)emit(s,"%s",date);
        emit(s,"\",\"front_id\":%ld,\"session_id\":%ld,\"status_bound\":%s,\"login_generation\":%lu}",user?front(user):0,user?session(user):0,object&&td&&md?"true":"false",s->login_generation);
        return;
    }
    int product=s->action==PRODUCT;
    Find get=(Find)(void *)GetProcAddress(core,product?"?GetProductLike@@YAPBUProduct@@PBD@Z":"?GetInstrumentIgnoreCase@@YAPBUInstrument@@PBD@Z");
    Text id=(Text)(void *)GetProcAddress(core,product?"?Product_Id@@YAPBDPBUProduct@@@Z":"?Instrument_Id@@YAPBDPBUInstrument@@@Z");
    Number exchange=(Number)(void *)GetProcAddress(core,product?"?Product_ExchangeID@@YAJPBUProduct@@@Z":"?Instrument_ExchangeId@@YAJPBUInstrument@@@Z");
    Number status=(Number)(void *)GetProcAddress(core,product?"?Product_Status@@YAJPBUProduct@@@Z":"?Instrument_Status@@YAJPBUInstrument@@@Z");
    if(!get||!id||!exchange||!status){s->error=40;return;}
    const void *item=get(s->argument);
    if(!item){
        int ready=has_metadata(core,product);if(ready<0){s->error=40;return;}
        emit(s,"{\"found\":false,\"data_ready\":%s}",ready?"true":"false");return;
    }
    emit(s,"{\"found\":true,\"data_ready\":true,\"id_hex\":\"");hex_text(s,id(item));
    emit(s,"\",\"exchange\":%ld,\"status\":%ld",exchange(item),status(item));
    if(!product){
        Text name=(Text)(void *)GetProcAddress(core,"?Instrument_Name@@YAPBDPBUInstrument@@@Z");
        if(!name){s->error=40;return;}
        emit(s,",\"name_hex\":\"");hex_text(s,name(item));emit(s,"\"");
        Amount tick=(Amount)(void *)GetProcAddress(core,"?Instrument_PriceTick@@YANPBUInstrument@@@Z");
        Amount lower=(Amount)(void *)GetProcAddress(core,"?Instrument_LowerLimitPrice@@YANPBUInstrument@@@Z");
        Amount upper=(Amount)(void *)GetProcAddress(core,"?Instrument_UpperLimitPrice@@YANPBUInstrument@@@Z");
        if(!tick||!lower||!upper){s->error=40;return;}
        emit(s,",\"tick\":%.17g,\"lower\":%.17g,\"upper\":%.17g",optional_price(tick(item)),optional_price(lower(item)),optional_price(upper(item)));
    }
    emit(s,"}");
}
