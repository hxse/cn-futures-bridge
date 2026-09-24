/* 使用真实 RichEdit/对话框验证空结算分支；不启动快期、不连接账户。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static ProbeState state;
static HWND main_window,status_window,dialog_window,body_window;
static unsigned confirmations;
static const WCHAR *all_day=L"快期2-CTP-上期技术-全天站点";
static const WCHAR *success=L"09:58:40\t查询结算单成功\r\n";

int readable(const void *pointer,size_t size){return 0;}
static void require(int value,const char *message){
    if(!value){fprintf(stderr,"FAIL: %s\n",message);exit(1);}
}
static void pump(void){
    MSG msg;
    while(PeekMessageW(&msg,NULL,0,0,PM_REMOVE)){TranslateMessage(&msg);DispatchMessageW(&msg);}
}
static INT_PTR CALLBACK dialog_proc(HWND w,UINT message,WPARAM wp,LPARAM lp){
    if(message==WM_COMMAND&&LOWORD(wp)==IDOK){confirmations++;ShowWindow(w,SW_HIDE);return TRUE;}
    return FALSE;
}
static void setup(const WCHAR *site,const WCHAR *status,const WCHAR *body){
    if(dialog_window)DestroyWindow(dialog_window);
    memset(&state,0,sizeof(state));confirmations=0;
    state.pid=GetCurrentProcessId();state.gui_thread=GetCurrentThreadId();
    wcscpy(state.main_titles[0],site);wcscpy(state.main_titles[1],site);
    SetWindowTextW(main_window,site);SetWindowTextW(status_window,status);
    struct {DLGTEMPLATE box;WORD menu,cls,title;} template={0};
    template.box.style=WS_POPUP|WS_CAPTION|WS_SYSMENU|DS_MODALFRAME;
    template.box.x=10;template.box.y=10;template.box.cx=240;template.box.cy=160;
    dialog_window=CreateDialogIndirectParamW(GetModuleHandleW(NULL),&template.box,main_window,dialog_proc,0);
    require(dialog_window!=NULL,"create dialog");SetWindowTextW(dialog_window,L"确认结算单");
    require(CreateWindowW(L"Button",L"确认",WS_CHILD|WS_VISIBLE,10,100,70,25,dialog_window,
                         (HMENU)IDOK,GetModuleHandleW(NULL),NULL)!=NULL,"confirm button");
    require(CreateWindowW(L"Button",L"取消",WS_CHILD|WS_VISIBLE,90,100,70,25,dialog_window,
                         (HMENU)IDCANCEL,GetModuleHandleW(NULL),NULL)!=NULL,"cancel button");
    body_window=CreateWindowExA(0,"RichEdit20A","",WS_CHILD|WS_VISIBLE|ES_MULTILINE|ES_READONLY,
                              0,0,200,90,dialog_window,(HMENU)7601,GetModuleHandleW(NULL),NULL);
    require(body_window!=NULL,"RichEdit body");SetWindowTextW(body_window,body);
    ShowWindow(dialog_window,SW_SHOW);pump();
}
static void stale_timer(void){
    state.empty_settlement_window=(DWORD)(uintptr_t)dialog_window;
    state.empty_settlement_since=GetTickCount64()-1000;
}
static void denied(const char *message){
    stale_timer();confirm_document(&state,dialog_window,1);pump();
    require(!state.settlement_count&&!state.empty_settlement_count&&!confirmations
            &&IsWindowVisible(dialog_window),message);
}
int wmain(void){
    require(LoadLibraryW(L"riched20.dll")!=NULL,"load RichEdit");
    WNDCLASSW cls={0};cls.lpfnWndProc=DefWindowProcW;cls.hInstance=GetModuleHandleW(NULL);cls.lpszClassName=L"CfbSettlementFixture";
    require(RegisterClassW(&cls)!=0,"register main");
    main_window=CreateWindowW(cls.lpszClassName,all_day,WS_OVERLAPPEDWINDOW|WS_VISIBLE,
                             0,0,800,600,NULL,NULL,cls.hInstance,NULL);
    require(main_window!=NULL,"create main");
    status_window=CreateWindowW(L"Static",success,WS_CHILD|WS_VISIBLE,0,500,600,30,
                               main_window,(HMENU)1257,cls.hInstance,NULL);
    require(status_window!=NULL,"create status");
    setup(all_day,success,L"");
    require(confirm_document(&state,dialog_window,1)==1&&!state.settlement_count,"empty waits initially");
    require(state.empty_settlement_window==(DWORD)(uintptr_t)dialog_window,"candidate established");
    require(confirm_document(&state,dialog_window,1)==1&&!state.settlement_count,"no immediate confirmation");
    Sleep(270);confirm_document(&state,dialog_window,1);
    require(state.settlement_count==1&&state.empty_settlement_count==1&&!confirmations,"single asynchronous confirmation");
    confirm_document(&state,dialog_window,1);
    require(state.settlement_count==1,"no repeated post while visible");pump();
    require(confirmations==1&&!IsWindowVisible(dialog_window),"confirmation closed window");
    reset_notice_state(&state);
    require(!state.empty_settlement_window&&!state.startup_last_window,"hidden window resets deduplication");
    ShowWindow(dialog_window,SW_SHOW);confirm_document(&state,dialog_window,1);
    require(state.settlement_count==1,"reused handle gets fresh waiting period");
    setup(all_day,L"09:58:40\t查询结算单失败\r\n",L"");denied("failed query not accepted");
    require(!state.empty_settlement_window,"failure clears timer");
    setup(all_day,L"09:58:40\t查询结算单成功，附加文字",L"");denied("partial status match rejected");
    setup(all_day,success,L"");ShowWindow(status_window,SW_HIDE);denied("missing visible status rejected");ShowWindow(status_window,SW_SHOW);
    setup(all_day,success,L"");
    HWND duplicate=CreateWindowW(L"Static",success,WS_CHILD|WS_VISIBLE,0,530,600,30,main_window,(HMENU)1257,cls.hInstance,NULL);
    require(duplicate!=NULL,"duplicate status");denied("ambiguous status rejected");DestroyWindow(duplicate);
    setup(all_day,success,L"");EnableWindow(GetDlgItem(dialog_window,IDOK),FALSE);denied("disabled confirm rejected");
    setup(L"快期2-CTP-上期技术-电信2",success,L"");denied("normal sandbox empty report rejected");
    setup(L"快期2-CTP-华安期货-一套",success,L"");denied("live empty report rejected");
    setup(all_day,success,L"正在加载");denied("partial nonempty report rejected");
    setup(all_day,success,L"");SetWindowTextW(dialog_window,L"确认下单");denied("trading confirmation rejected");
    setup(all_day,success,L"");
    SendMessageW(body_window,EM_SETREADONLY,FALSE,0);denied("writable body rejected");
    setup(all_day,success,L"");confirm_document(&state,dialog_window,1);stale_timer();
    SetWindowTextW(status_window,L"09:58:40\t查询结算单失败\r\n");confirm_document(&state,dialog_window,1);
    SetWindowTextW(status_window,success);confirm_document(&state,dialog_window,1);
    require(!state.settlement_count,"interrupted conditions restart stability period");
    WCHAR report[256];wcscpy(report,L"结算单 客户号 资金状况 Client ID ");
    size_t length=wcslen(report);while(length<200)report[length++]=L' ';report[length]=0;
    setup(L"快期2-CTP-上期技术-电信2",success,report);confirm_document(&state,dialog_window,1);
    require(state.settlement_count==1&&!state.empty_settlement_count,"full report keeps original validation");pump();
    require(confirmations==1,"full report confirmed once");
    DestroyWindow(dialog_window);DestroyWindow(main_window);
    puts("PASS: empty settlement scope, controls, completion, stability, deduplication and full-report validation");
    return 0;
}
