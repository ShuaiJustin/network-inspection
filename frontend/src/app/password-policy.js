export const ADMIN_PASSWORD_MIN_LENGTH = 8;
export const ADMIN_PASSWORD_MAX_LENGTH = 256;

export function validateAdminCredentials({ username, password, confirmPassword }) {
  if (!username || !username.trim()) return "请输入用户名";
  if (!password) return "请输入密码";
  if (password.length < ADMIN_PASSWORD_MIN_LENGTH) return `密码至少需要 ${ADMIN_PASSWORD_MIN_LENGTH} 位字符`;
  if (password.length > ADMIN_PASSWORD_MAX_LENGTH) return `密码不能超过 ${ADMIN_PASSWORD_MAX_LENGTH} 位字符`;
  if (confirmPassword !== undefined && password !== confirmPassword) return "两次输入的密码不一致";
  return null;
}