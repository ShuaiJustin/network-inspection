export function shouldResetLocalSession(status, requestToken, currentToken) {
  return status === 401 && Boolean(requestToken) && requestToken === currentToken;
}
