// The banner used to guess its tone by matching the message text, so a
// finished action ("巡检任务…已创建，正在执行") was classified as work still in
// flight by a /正在执行/ rule and never cleared itself. Wording changes kept
// breaking the guess, so the caller now states the tone and this module only
// decides how long each tone stays.

// Success is the only outcome that needs no follow-up, so it is the only one
// that disappears on its own. Progress tracks work that is still running,
// warnings list rows the operator has to fix, and errors must be read, so all
// three stay until dismissed.
const AUTO_DISMISS_MS = 4000;

export function noticeAutoDismissMs(tone) {
  return tone === "success" ? AUTO_DISMISS_MS : null;
}