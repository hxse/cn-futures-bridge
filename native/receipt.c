/* 固定版本实际报单引用捕获；原交易只由快期正常快捷键路径调用一次。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

typedef void (__cdecl *SendParked)(const void *);
typedef long (__cdecl *Number)(const void *);
static SendParked real_send;
static Number parked_id;
static void **send_slot,**callback_slot;
static void *observer_vtable[4];
static struct {void **vtable;} observer={observer_vtable};
static DWORD owner_thread;
static long expected,scope_id;
static int installed,active,send_count,count,broken;
static struct {
    long parked_id,direction,offset,volume,time_condition,selector;
    double price;
    int valid;
    char instrument[81],order_ref[13],source[81];
} receipt;

static int replace_pointer(void **slot,void *from,void *to){
    DWORD previous,unused;
    if(!readable(slot,sizeof(*slot))||*slot!=from||!VirtualProtect(slot,sizeof(*slot),PAGE_READWRITE,&previous))return 0;
    void *found=InterlockedCompareExchangePointer((void *volatile *)slot,to,from);
    int restored=VirtualProtect(slot,sizeof(*slot),previous,&unused)!=0;
    return found==from&&restored;
}

static int copy_string(const BYTE *request,size_t offset,char *output,size_t maximum){
    const BYTE *string=*(const BYTE *const *)(request+offset);
    if(!readable(string,24))return 0;
    DWORD length=*(const DWORD *)(string+16),capacity=*(const DWORD *)(string+20);
    const char *text=capacity<16?(const char *)string:*(const char *const *)string;
    if(length>=maximum||capacity<length||!readable(text,length+1)||text[length])return 0;
    memcpy(output,text,length+1);return 1;
}

static void __fastcall observe_callback(void *object,void *unused,const void *request){
    DWORD saved=GetLastError();
    if(object!=&observer||!expected||scope_id!=expected||GetCurrentThreadId()!=owner_thread){broken=1;return;}
    if(++count!=1){broken=1;return;}
    memset(&receipt,0,sizeof(receipt));receipt.parked_id=scope_id;
    const BYTE *p=request,*core=(const BYTE *)GetModuleHandleA("shinny_future_core.dll");
    if(readable(p,0x70)&&*(const void *const *)p==core+0x67e544){
        receipt.direction=*(const long *)(p+0x14);receipt.offset=*(const long *)(p+0x20);
        receipt.price=*(const double *)(p+0x18);receipt.selector=*(const long *)(p+0x24);
        receipt.time_condition=*(const long *)(p+0x28);receipt.volume=*(const long *)(p+0x2c);
        receipt.valid=isfinite(receipt.price)&&(*(const DWORD *)(p+0x68)&0x8000)!=0&&
            copy_string(p,0x0c,receipt.instrument,sizeof(receipt.instrument))&&
            copy_string(p,0x48,receipt.order_ref,sizeof(receipt.order_ref))&&
            copy_string(p,0x60,receipt.source,sizeof(receipt.source))&&receipt.order_ref[0];
        for(const char *r=receipt.order_ref;receipt.valid&&*r;r++)if(*r<'0'||*r>'9')receipt.valid=0;
    }
    SetLastError(saved);
}

static void __cdecl observe_send(const void *parked){
    DWORD saved=GetLastError();long previous=scope_id;
    int ours=expected&&GetCurrentThreadId()==owner_thread&&parked_id(parked)==expected;
    int observing=0;
    if(ours){
        scope_id=expected;send_count++;active++;
        observing=replace_pointer(callback_slot,NULL,&observer);
        if(!observing)broken=1;
    }
    SetLastError(saved);real_send(parked);saved=GetLastError();
    if(ours){
        if(observing&&!replace_pointer(callback_slot,&observer,NULL))broken=1;
        scope_id=previous;active--;
    }
    SetLastError(saved);
}

int receipt_stop(void){
    if(!installed)return 1;
    if(active)return 0;
    expected=0;
    if(*callback_slot==&observer&&!replace_pointer(callback_slot,&observer,NULL))return 0;
    if(*callback_slot!=NULL)return 0;
    if(*send_slot==(void *)observe_send){
        if(!replace_pointer(send_slot,(void *)observe_send,(void *)real_send))return 0;
    }else if(*send_slot!=(void *)real_send)return 0;
    installed=0;return 1;
}

static int arm(long id){
    BYTE *main=(BYTE *)GetModuleHandleW(NULL),*core=(BYTE *)GetModuleHandleA("shinny_future_core.dll");
    if(installed||!main||!core||id<=0)return 0;
    real_send=(SendParked)(void *)GetProcAddress((HMODULE)core,"?ShinnyCore_ReqParkedOrderSend@@YAXPBUParkedOrder@@@Z");
    parked_id=(Number)(void *)GetProcAddress((HMODULE)core,"?ParkedOrder_Id@@YAJPBUParkedOrder@@@Z");
    callback_slot=(void **)(core+0x78f3b0);send_slot=(void **)(main+0x819b00);
    if(!real_send||!parked_id||!readable(callback_slot,4)||!readable(send_slot,4))return 0;
    if(*callback_slot!=NULL||*send_slot!=(void *)real_send)return 0;
    /* 该固定同步发送分支只调用槽 1，返回即恢复空回调，不注册持久 C++ 对象。 */
    observer_vtable[1]=(void *)observe_callback;
    owner_thread=GetCurrentThreadId();expected=id;scope_id=0;
    active=send_count=count=broken=0;memset(&receipt,0,sizeof(receipt));
    installed=1;
    if(!replace_pointer(send_slot,(void *)real_send,(void *)observe_send)){
        if(!receipt_stop())return -1;
        return 0;
    }
    return 1;
}

void receipt_query(ProbeState *s,HWND window){
    if(!matches_main(s,window)){s->error=43;return;}
    if(!strncmp(s->argument,"arm ",4)){
        char *end;long id=strtol(s->argument+4,&end,10);
        if(*end||id<=0){s->error=44;return;}
        int result=arm(id);if(result!=1){s->error=result<0?71:70;return;}
    }else if(!strcmp(s->argument,"finish")){
        if(!receipt_stop()){s->error=71;return;}
    }else {s->error=44;return;}
    emit(s,"{\"armed\":%s,\"active\":%d,\"send_count\":%d,\"count\":%d,\"broken\":%s,\"valid\":%s,\"parked_id\":%ld,\"direction\":%ld,\"offset\":%ld,\"volume\":%ld,\"price\":%.17g,\"time_condition\":%ld,\"selector\":%ld,\"instrument_hex\":\"",
         installed?"true":"false",active,send_count,count,broken?"true":"false",receipt.valid?"true":"false",
         receipt.parked_id,receipt.direction,receipt.offset,receipt.volume,isfinite(receipt.price)?receipt.price:0,receipt.time_condition,receipt.selector);
    hex_text(s,receipt.instrument);emit(s,"\",\"order_ref_hex\":\"");hex_text(s,receipt.order_ref);
    emit(s,"\",\"source_hex\":\"");hex_text(s,receipt.source);emit(s,"\"}");
}
