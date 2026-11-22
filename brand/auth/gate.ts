// Sign-in with Adaflow for the cloud server. Runs before the app itself is loaded (see vite-plugin.mjs) and
// decides whether it may start; the upstream app never learns that a login exists.
//
// The server says whether sign-in is on (GET /api/nix/auth/config). The desktop app and local installs run
// upstream's server, which has no such route: the answer is not that JSON, and the app starts at once.
//
// This screen is a convenience, not the lock: the lock is the server refusing /api/* without its session cookie
// (backend/nix/gate.py). Adaflow's token lives in sessionStorage, managed by the SDK, and is sent to our server
// only to open or renew that cookie session.
import { createSsoSession, type SsoSession } from '@adaflow/sdk'
import markSvg from '../assets/mark.svg?raw'

interface User { id: string; email: string | null; name: string | null }
interface Config { enabled: true; adaflowUrl: string; user: User | null }

const AUTH = '/api/nix/auth'
const CALLBACK = '/auth/callback'
const RETURN_TO = 'nix:returnTo'
const KEEP_ALIVE_MS = 10 * 60 * 1000 // the cookie lasts 30 minutes; renewing it also re-checks the Adaflow session

const pt = (localStorage.getItem('remiqora_locale') ?? navigator.language).toLowerCase().startsWith('pt')
const T = pt
  ? {
      lead: 'Entre com a sua conta Adaflow para usar o estúdio.',
      signIn: 'Entrar com Adaflow',
      working: 'Entrando…',
      lost: 'Sua sessão terminou. Entre novamente.',
      failed: 'Não foi possível concluir a entrada. Tente novamente.',
      unavailable: 'O Adaflow não respondeu. Tente novamente em instantes.',
      forbidden: 'A sua organização não tem acesso a este estúdio.',
      signOut: 'Sair',
      account: 'Conta',
    }
  : {
      lead: 'Sign in with your Adaflow account to use the studio.',
      signIn: 'Sign in with Adaflow',
      working: 'Signing in…',
      lost: 'Your session ended. Sign in again.',
      failed: 'Sign-in could not be completed. Try again.',
      unavailable: 'Adaflow did not answer. Try again in a moment.',
      forbidden: 'Your organization has no access to this studio.',
      signOut: 'Sign out',
      account: 'Account',
    }

async function loadConfig(): Promise<Config | null> {
  try {
    const resp = await fetch(`${AUTH}/config`, { signal: AbortSignal.timeout(4000), headers: { accept: 'application/json' } })
    if (!resp.ok || !resp.headers.get('content-type')?.includes('application/json')) return null
    const body = await resp.json()
    return body?.enabled === true && typeof body.adaflowUrl === 'string' ? body : null
  } catch {
    return null
  }
}

type Opened = { user: User } | { error: 'forbidden' | 'unavailable' | 'failed' }

/** Trades Adaflow's token for our server's cookie session. `session.fetch` renews an expired token first. */
async function openServerSession(session: SsoSession): Promise<Opened> {
  if (!session.getJwt()) return { error: 'failed' }
  try {
    const resp = await session.fetch(`${AUTH}/session`, { method: 'POST' })
    if (resp.ok) return { user: (await resp.json()).user }
    return { error: resp.status === 403 ? 'forbidden' : resp.status === 503 ? 'unavailable' : 'failed' }
  } catch {
    return { error: 'unavailable' }
  }
}

// ---- screens: plain DOM with their own styles, because the app's stylesheet is not loaded yet ----

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, css: string, text?: string): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag)
  node.style.cssText = css
  if (text !== undefined) node.textContent = text
  return node
}
const FONT = "font:15px/1.5 'Inter',system-ui,'Segoe UI',Roboto,sans-serif;"
const BUTTON = `${FONT}font-weight:600;color:#fff;background:linear-gradient(135deg,#b9722a,#a9512b);border:0;border-radius:10px;padding:11px 20px;cursor:pointer;`

function showSignIn(session: SsoSession, message?: string, busy = false): void {
  const host = document.getElementById('app') ?? document.body
  const screen = el('main', `${FONT}position:fixed;inset:0;display:grid;place-items:center;padding:24px;background:#15110d;color:#efe6d6;`)
  const card = el('div', 'width:100%;max-width:380px;display:flex;flex-direction:column;align-items:center;gap:14px;padding:36px 28px;text-align:center;background:#201a14;border:1px solid #40332a;border-radius:16px;')
  const mark = el('div', 'width:56px;height:56px;')
  mark.innerHTML = markSvg
  mark.setAttribute('aria-hidden', 'true')
  card.append(mark, el('h1', 'margin:0;font-size:22px;font-weight:700;', 'NiX Studio'), el('p', 'margin:0;color:#ab9c88;', busy ? T.working : T.lead))
  if (message) {
    const note = el('p', 'margin:0;color:#e9c27d;font-size:14px;', message)
    note.setAttribute('role', 'alert')
    card.append(note)
  }
  if (!busy) {
    const button = el('button', `${BUTTON}margin-top:6px;width:100%;`, T.signIn)
    button.type = 'button'
    button.addEventListener('click', () => {
      // where to come back to; the callback page itself is never a destination
      if (location.pathname !== CALLBACK) sessionStorage.setItem(RETURN_TO, location.pathname + location.search)
      session.login()
    })
    card.append(button)
    queueMicrotask(() => button.focus())
  }
  screen.append(card)
  host.replaceChildren(screen)
}

function mountAccount(session: SsoSession, user: User): void {
  const label = user.name || user.email || T.account
  const wrap = el('div', `${FONT}position:fixed;right:12px;bottom:12px;z-index:60;`)
  const chip = el('button', 'width:32px;height:32px;border-radius:50%;border:1px solid #40332a;background:#2a2219;color:#e9c27d;font:600 13px Inter,system-ui,sans-serif;cursor:pointer;', label.trim().charAt(0).toUpperCase())
  chip.type = 'button'
  chip.title = label
  chip.setAttribute('aria-label', `${T.account}: ${label}`)
  chip.setAttribute('aria-expanded', 'false')
  const menu = el('div', 'position:absolute;right:0;bottom:40px;min-width:220px;display:none;flex-direction:column;gap:2px;padding:12px;background:#201a14;border:1px solid #40332a;border-radius:12px;box-shadow:0 8px 24px rgb(0 0 0/.5);color:#efe6d6;')
  if (user.name) menu.append(el('strong', 'font-size:14px;', user.name))
  if (user.email) menu.append(el('span', 'font-size:13px;color:#ab9c88;overflow-wrap:anywhere;', user.email))
  const out = el('button', `${FONT}margin-top:10px;padding:7px 12px;font-size:14px;color:#efe6d6;background:#2a2219;border:1px solid #40332a;border-radius:8px;cursor:pointer;`, T.signOut)
  out.type = 'button'
  out.addEventListener('click', () => void signOut(session))
  menu.append(out)
  const toggle = (open: boolean) => {
    menu.style.display = open ? 'flex' : 'none'
    chip.setAttribute('aria-expanded', String(open))
  }
  chip.addEventListener('click', () => toggle(menu.style.display === 'none'))
  document.addEventListener('click', (event) => { if (!wrap.contains(event.target as Node)) toggle(false) })
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') toggle(false) })
  wrap.append(menu, chip)
  document.body.append(wrap)
}

async function signOut(session: SsoSession, reason?: 'lost'): Promise<void> {
  await fetch(`${AUTH}/logout`, { method: 'POST' }).catch(() => undefined)
  session.logout()
  if (reason) sessionStorage.setItem('nix:notice', reason)
  location.reload()
}

function keepAlive(session: SsoSession): void {
  let last = Date.now()
  const renew = async () => {
    last = Date.now()
    const opened = await openServerSession(session)
    // an outage is not a dead session: keep the cookie and try again on the next tick
    if ('error' in opened && opened.error !== 'unavailable') await signOut(session, 'lost')
  }
  setInterval(() => void renew(), KEEP_ALIVE_MS)
  // timers are throttled in a background tab: catch up as soon as the tab is looked at again
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - last > KEEP_ALIVE_MS) void renew()
  })
}

/** Resolves to true when the app may start. */
export default async function gate(): Promise<boolean> {
  const config = await loadConfig()
  if (!config) return true // no sign-in on this server: desktop, local install, or upstream's entry point

  const session = createSsoSession({
    adaflowUrl: config.adaflowUrl,
    authUrl: `${location.origin}${AUTH}/gw`, // the SDK's session routes, relayed by our server
    callbackUrl: CALLBACK,
    // automatic renewal gave up: back to the sign-in screen, never straight to the handoff (that is the loop
    // the SDK's guard exists to prevent)
    onSessionLost: () => void signOut(session, 'lost'),
  })
  const start = (user: User): true => {
    mountAccount(session, user)
    keepAlive(session)
    return true
  }
  const messages = { forbidden: T.forbidden, unavailable: T.unavailable, failed: T.failed }

  if (location.pathname === CALLBACK) {
    showSignIn(session, undefined, true)
    const opened = session.completeLogin() ? await openServerSession(session) : ({ error: 'failed' } as const)
    if ('error' in opened) {
      if (opened.error === 'forbidden') session.logout()
      showSignIn(session, messages[opened.error])
      return false
    }
    const back = sessionStorage.getItem(RETURN_TO)
    sessionStorage.removeItem(RETURN_TO)
    // a full load, so the app starts from a clean URL with the session in place; same-origin paths only
    location.replace(back && back.startsWith('/') && !back.startsWith('//') ? back : '/')
    return false
  }

  if (config.user) return start(config.user)
  // the cookie ran out but Adaflow's token (or its 7-day session) may still be good: no need to ask again
  if (session.getJwt()) {
    const opened = await openServerSession(session)
    if ('user' in opened) return start(opened.user)
    if (opened.error !== 'failed') {
      showSignIn(session, messages[opened.error])
      return false
    }
  }
  const notice = sessionStorage.getItem('nix:notice')
  sessionStorage.removeItem('nix:notice')
  showSignIn(session, notice === 'lost' ? T.lost : undefined)
  return false
}
