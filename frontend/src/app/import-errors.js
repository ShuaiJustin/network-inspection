import { userErrorMessage } from "./api-messages.js";

export function importIssueRows(errors) {
  return errors.map((error) => ({
    row: Number(error.row),
    reason: userErrorMessage(String(error.reason ?? "导入数据无效"), "该行数据无效，请核对后重试"),
  }));
}
