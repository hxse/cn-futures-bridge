/* 单个常驻控制器，持有 hook 和共享区；超时不会释放仍在运行的调用。 */
#include "probe.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static HWND main_window;
static DWORD target_pid,target_thread;
static WCHAR *main_titles[2];
static BOOL CALLBACK locate(HWND w,LPARAM unused){
    WCHAR title[256];GetWindowTextW(w,title,256);DWORD pid;DWORD thread=GetWindowThreadProcessId(w,&pid);
    if(target_pid&&pid!=target_pid)return TRUE;
    if(IsWindowVisible(w)&&(wcsstr(title,main_titles[0])||wcsstr(title,main_titles[1])||!wcscmp(title,L"用户登录"))){
        main_window=w;target_pid=pid;target_thread=thread;return FALSE;
    }
    return TRUE;
}
static void print_hex(const char *text){for(const unsigned char *p=(const void *)text;*p;p++)printf("%02x",*p);}
int wmain(int argc,WCHAR **argv){
    if(argc!=7||!argv[4][0]||!argv[5][0]||wcslen(argv[4])>=160||wcslen(argv[5])>=160)return 2;
    main_titles[0]=argv[4];main_titles[1]=argv[5];
    DWORD timeout=(DWORD)_wtoi(argv[3]);if(timeout<1||timeout>300000)return 2;
    DWORD startup_timeout=(DWORD)_wtoi(argv[6]);if(startup_timeout<1||startup_timeout>300000)return 2;
    EnumWindows(locate,0);if(!main_window){puts("{\"ready\":false}");return 1;}
    HMODULE dll=LoadLibraryW(argv[1]);
    if(!dll){printf("{\"ready\":false,\"error\":3,\"win_error\":%lu}\n",GetLastError());return 3;}
    HANDLE mapping=CreateFileMappingW(INVALID_HANDLE_VALUE,NULL,PAGE_READWRITE,0,sizeof(ProbeState),PROBE_MAP);
    if(!mapping||GetLastError()==ERROR_ALREADY_EXISTS)return 4;
    ProbeState *s=MapViewOfFile(mapping,FILE_MAP_ALL_ACCESS,0,0,sizeof(*s));if(!s)return 5;
    memset(s,0,sizeof(*s));s->magic=PROBE_MAGIC;s->pid=target_pid;
    s->startup_active=1;s->startup_until=GetTickCount64()+startup_timeout;
    wcscpy(s->main_titles[0],main_titles[0]);wcscpy(s->main_titles[1],main_titles[1]);
    if(!WideCharToMultiByte(CP_ACP,WC_NO_BEST_FIT_CHARS,argv[2],-1,s->root,sizeof(s->root),NULL,NULL))return 6;
    UINT msg=RegisterWindowMessageW(PROBE_MESSAGE);
    HHOOK hook=SetWindowsHookExW(WH_CALLWNDPROC,(HOOKPROC)GetProcAddress(dll,"CfbHook"),dll,target_thread);
    if(!hook)return 7;
    printf("{\"ready\":true,\"pid\":%lu,\"hwnd\":%lu}\n",target_pid,(DWORD)(uintptr_t)main_window);fflush(stdout);
    char line[4096],command[32],argument[2048];unsigned long target;
    BOOL outstanding=FALSE;
    while(fgets(line,sizeof(line),stdin)){
        command[0]=argument[0]=0;target=0;sscanf(line,"%31s",command);
        DWORD action=0;
        if(!strcmp(command,"windows"))action=WINDOWS;
        else if(!strcmp(command,"managed_windows"))action=MANAGED_WINDOWS;
        else if(!strcmp(command,"gui_state"))action=GUI_STATE;
        else if(!strcmp(command,"recover_gui"))action=GUI_RECOVER;
        else if(!strcmp(command,"session"))action=SESSION;
        else if(!strcmp(command,"product"))action=PRODUCT;
        else if(!strcmp(command,"instrument"))action=INSTRUMENT;
        else if(!strcmp(command,"parked"))action=PARKED;
        else if(!strcmp(command,"receipt"))action=RECEIPT;
        else if(!strcmp(command,"track"))action=TRACKING;
        else if(!strcmp(command,"menu"))action=MENU;
        else if(!strcmp(command,"export"))action=EXPORT;
        else if(!strcmp(command,"import"))action=IMPORT;
        else if(!strcmp(command,"inspect"))action=INSPECT;
        else if(!strcmp(command,"select"))action=SELECT_ROW;
        else if(!strcmp(command,"focus"))action=FOCUS;
        else if(!strcmp(command,"set_text"))action=SET_TEXT;
        else if(!strcmp(command,"combo"))action=SELECT_COMBO;
        else if(!strcmp(command,"startup_done"))action=STARTUP_DONE;
        else if(!strcmp(command,"quit"))action=STOP;
        if(outstanding&&!s->done){puts("{\"error\":90,\"done\":false,\"data\":{}}");fflush(stdout);continue;}
        outstanding=FALSE;
        if(!action){puts("{\"error\":99,\"done\":true,\"data\":{}}");fflush(stdout);continue;}
        DWORD previous_thread=target_thread;
        do{
            main_window=NULL;EnumWindows(locate,0);
            if(main_window||(action!=WINDOWS&&action!=STARTUP_DONE&&action!=MANAGED_WINDOWS&&action!=GUI_STATE)||!s->startup_active||GetTickCount64()>=s->startup_until)break;
            /* 登录框关闭与主窗口设置标题之间允许短暂空档，不更换进程或 GUI 线程。 */
            Sleep(10);
        }while(TRUE);
        if(!main_window||target_thread!=previous_thread){puts("{\"error\":91,\"done\":true,\"data\":{}}");fflush(stdout);break;}
        if(action==SESSION||action==PRODUCT||action==INSTRUMENT||action==PARKED||action==RECEIPT||action==TRACKING)sscanf(line,"%31s %2047[^\r\n]",command,argument);
        else if(action!=WINDOWS&&action!=STOP)sscanf(line,"%31s %lu %2047[^\r\n]",command,&target,argument);
        HWND window=target?(HWND)(uintptr_t)target:main_window;DWORD pid=0;
        GetWindowThreadProcessId(window,&pid);
        if(pid!=target_pid){puts("{\"error\":92,\"done\":true,\"data\":{}}");fflush(stdout);continue;}
        s->action=action;s->target=(DWORD)(uintptr_t)window;s->done=s->error=0;
        s->created_dialogs=s->export_result=s->import_completed=s->restored=0;s->export_ms=0;
        s->message_text[0]=s->message_title[0]=s->path[0]=s->argument[0]=s->output[0]=0;
        s->selected=-1;s->selected_count=s->row_count=0;
        if(action==EXPORT||action==IMPORT)snprintf(s->path,sizeof(s->path),"%s",argument);
        else snprintf(s->argument,sizeof(s->argument),"%s",argument);
        DWORD call_timeout=timeout;
        ULONGLONG now=GetTickCount64();
        if((action==WINDOWS||action==STARTUP_DONE||action==MANAGED_WINDOWS||action==GUI_STATE)&&s->startup_active&&s->startup_until>now+call_timeout)
            call_timeout=(DWORD)(s->startup_until-now);
        DWORD_PTR result;BOOL sent=SendMessageTimeoutW(window,msg,0,0,SMTO_ABORTIFHUNG,call_timeout,&result);
        outstanding=!sent||!s->done;
        if(outstanding)s->error=90;
        printf("{\"error\":%lu,\"done\":%s,\"export_result\":%lu,\"import_completed\":%lu,\"restored\":%lu,\"dialogs_created\":%lu,\"native_ms\":%.4f,\"columns\":%lu,\"selected\":%ld,\"selected_count\":%ld,\"row_count\":%ld,\"message_hex\":\"",
               s->error,s->done?"true":"false",s->export_result,s->import_completed,s->restored,s->created_dialogs,s->export_ms,s->columns,s->selected,s->selected_count,s->row_count);
        print_hex(s->message_text);
        printf("\",\"startup_privacy_count\":%lu,\"startup_terms_count\":%lu,\"startup_wizard_count\":%lu,\"settlement_count\":%lu,\"information_close_count\":%lu,\"empty_settlement_count\":%lu,",
               s->startup_privacy_count,s->startup_terms_count,s->startup_wizard_count,s->settlement_count,s->information_close_count,s->empty_settlement_count);
        printf("\"order_notice_count\":%lu,\"order_notice_kind\":%lu,\"order_notice_hex\":\"",s->order_notice_count,s->order_notice_kind);
        print_hex(s->order_notice_text);printf("\",");
        printf("\"trade_notice_check_count\":%lu,\"trade_notice_checked_count\":%lu,\"trade_notice_confirm_count\":%lu,\"trade_notice_closed_count\":%lu,\"data\":%s}\n",
               s->trade_notice_check_count,s->trade_notice_checked_count,s->trade_notice_confirm_count,s->trade_notice_closed_count,
               s->output[0]&&!s->error?s->output:"{}");fflush(stdout);
        SecureZeroMemory(argument,sizeof(argument));if(!outstanding)SecureZeroMemory(s->argument,sizeof(s->argument));
        if(action==STOP&&!outstanding)break;
    }
    /* stdin 关闭也不能释放仍被 GUI 使用的映射；容器停止会终止整个 Wine 会话。 */
    while(outstanding&&!s->done)Sleep(50);
    if(s->action!=STOP&&main_window){s->action=STOP;s->target=(DWORD)(uintptr_t)main_window;DWORD_PTR result;SendMessageTimeoutW(main_window,msg,0,0,SMTO_ABORTIFHUNG,timeout,&result);}
    UnhookWindowsHookEx(hook);UnmapViewOfFile(s);CloseHandle(mapping);FreeLibrary(dll);return 0;
}
