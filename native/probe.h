#ifndef CFB_NATIVE_H
#define CFB_NATIVE_H
#include <windows.h>
#include <stdint.h>
#define PROBE_MAP L"Local\\CNFB_Terminal_20260924_6"
#define PROBE_MESSAGE L"CNFB_Terminal_20260924_6"
#define PROBE_MAGIC 0x434e4642u
enum { INSPECT=1, EXPORT=2, ARM=3, COUNTS=4, STOP=5, IMPORT=6, SESSION=7, PRODUCT=8, INSTRUMENT=9, SELECT_ROW=10, WINDOWS=11, FOCUS=12, SET_TEXT=13, SELECT_COMBO=14, STARTUP_DONE=15, MANAGED_WINDOWS=16, PARKED=17, MENU=18, RECEIPT=19, TRACKING=20, GUI_STATE=21, GUI_RECOVER=22 };
typedef struct {
    DWORD magic, pid, action, target, done, error, win_error;
    DWORD procedure, userdata, object, vtable, object_window, columns;
    DWORD created_dialogs, export_result, gui_thread;
    double export_ms;
    BYTE procedure_bytes[16];
    char path[240], root[240], argument[1024];
    LONG selected, selected_count, row_count;
    char output[131072];
    DWORD open_calls, message_calls, message_flags, atl_consumed, restored, import_completed;
    char message_text[4096], message_title[256];
    WCHAR main_titles[2][160];
    DWORD login_generation;
    DWORD startup_active,startup_privacy_count,startup_terms_count,startup_wizard_count,settlement_count,information_close_count;
    DWORD startup_last_window,startup_last_kind;
    DWORD trade_notice_count,trade_notice_check_count,trade_notice_checked_count;
    DWORD trade_notice_confirm_count,trade_notice_closed_count,trade_notice_window,trade_notice_phase;
    DWORD order_notice_count,order_notice_kind;
    char order_notice_text[4096];
    DWORD empty_settlement_window,empty_settlement_count;
    ULONGLONG empty_settlement_since;
    ULONGLONG startup_until;
} ProbeState;
void import_csv(ProbeState *request);
int readable(const void *pointer, size_t size);
void native_query(ProbeState *request, HWND window);
void gui_query(ProbeState *request, HWND window);
void market_query(ProbeState *request, HWND window);
void receipt_query(ProbeState *request, HWND window);
int receipt_stop(void);
void tracking_query(ProbeState *request, HWND window);
void readiness_query(ProbeState *request, HWND window);
void reset_notice_state(ProbeState *request);
void emit(ProbeState *request, const char *format, ...);
void hex_text(ProbeState *request, const char *value);
uintptr_t window_object(HWND window, int dialog);
int valid_path(ProbeState *request);
int matches_main(ProbeState *request,HWND window);
int confirm_document(ProbeState *request,HWND window,int allow_runtime);
#endif
