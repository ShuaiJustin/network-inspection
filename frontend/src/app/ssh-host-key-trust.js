export function formatSshHostKeyTrustPrompt({ ip, hostKeyAlgorithm, hostFingerprint, changed }) {
  const state = changed ? "发生变化" : "尚未受信任";
  return `${ip} 的 SSH 主机密钥${state}。请通过独立渠道核对设备身份后再确认。\n\n主机密钥算法：${hostKeyAlgorithm}\nSHA256 指纹：${hostFingerprint}\n\n确认信任此指纹吗？`;
}
