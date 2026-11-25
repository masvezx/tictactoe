import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { apiErrorKey, languages, translate, translations } from './i18n.js';
import './styles.css';

const EMPTY_BOARD = Array(9).fill(null);
const SESSION_KEY = 'tic-tac-toe-session';
const THEMES = ['light', 'dark', 'grid', 'ruled'];

function readStored(key, fallback) {
  const raw = localStorage.getItem(key);
  return raw ? JSON.parse(raw) : fallback;
}

class ApiError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

async function request(path, { token, body, signal } = {}) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    throw new ApiError(response.status);
  }
  return response.json();
}

function Mark({ value }) {
  return value ? (
    <svg className={`mark mark-${value.toLowerCase()}`} viewBox="0 0 100 100" aria-hidden="true">
      {value === 'X' ? <path d="M26 25 L75 76 M75 24 L25 75" /> : <circle cx="50" cy="50" r="29" />}
    </svg>
  ) : null;
}

function App() {
  const invitation = new URLSearchParams(window.location.search).get('invite');
  const [language, setLanguage] = useState(() => {
    const saved = readStored('tic-tac-toe-language', 'ru');
    return languages.includes(saved) ? saved : 'ru';
  });
  const t = (key, values) => translate(language, key, values);
  const [rulesOpen, setRulesOpen] = useState(false);
  const rulesButton = useRef(null);
  const [theme, setTheme] = useState(() => {
    const saved = readStored('tic-tac-toe-theme', 'light');
    return THEMES.includes(saved) ? saved : 'light';
  });
  const [session, setSession] = useState(() => {
    const saved = readStored(SESSION_KEY, null);
    return saved && (!invitation || saved.invitation === invitation) ? saved : null;
  });
  const [game, setGame] = useState(null);
  const [mode, setMode] = useState(invitation ? 'friend' : session?.mode || 'computer');
  const modeRestored = useRef(Boolean(invitation || session?.mode || !session));
  const [difficulty, setDifficulty] = useState('medium');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [inviteLoading, setInviteLoading] = useState(Boolean(invitation));
  const [refreshKey, setRefreshKey] = useState(0);
  const generation = useRef(0);
  const busy = useRef(false);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; }, []);
  useEffect(() => {
    document.documentElement.lang = language;
    document.title = translations[language].title;
    localStorage.setItem('tic-tac-toe-language', JSON.stringify(language));
  }, [language]);
  useEffect(() => {
    if (!rulesOpen) return;
    function closeOnEscape(event) {
      if (event.key === 'Escape') {
        setRulesOpen(false);
        rulesButton.current?.focus();
      }
    }
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [rulesOpen]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('tic-tac-toe-theme', JSON.stringify(theme));
  }, [theme]);

  useEffect(() => {
    if (!session && !invitation) return;
    const controller = new AbortController();
    const version = generation.current;
    let timer;
    async function refresh() {
      try {
        const state = await request(
          session ? `/api/games/${session.game_id}` : `/api/invitations/${encodeURIComponent(invitation)}`,
          { token: session?.player_token, signal: controller.signal },
        );
        if (version !== generation.current || controller.signal.aborted) return;
        setGame(state);
        if (!modeRestored.current) {
          setMode(state.mode);
          modeRestored.current = true;
        }
        setInviteLoading(false);
        if (!busy.current) setError(null);
        if (state.status === 'waiting' || state.status === 'playing') {
          timer = setTimeout(refresh, 1000);
        }
      } catch (err) {
        if (controller.signal.aborted || version !== generation.current) return;
        setInviteLoading(false);
        setError(err instanceof ApiError ? { key: apiErrorKey(err.status), values: { status: err.status } } : { key: 'connectionError' });
        if (!(err instanceof ApiError && [401, 404].includes(err.status))) {
          timer = setTimeout(refresh, 2000);
        }
      }
    }
    refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [session, invitation, refreshKey]);

  function saveSession(data, inviteToken, url) {
    const saved = {
      game_id: data.game.game_id,
      player_token: data.player_token,
      your_mark: data.your_mark,
      invitation: inviteToken,
      invitation_url: url,
      mode: data.game.mode,
    };
    localStorage.setItem(SESSION_KEY, JSON.stringify(saved));
    setSession(saved);
    setGame(data.game);
    setCopied(false);
    setMode(data.game.mode);
    modeRestored.current = true;
  }

  async function action(operation) {
    if (busy.current) return;
    busy.current = true;
    generation.current += 1;
    setPending(true);
    setError(null);
    try {
      await operation();
    } catch (err) {
      setError(err instanceof ApiError ? { key: apiErrorKey(err.status), values: { status: err.status } } : { key: 'actionError' });
    } finally {
      busy.current = false;
      if (mounted.current) {
        setPending(false);
        setRefreshKey((current) => current + 1);
      }
    }
  }

  async function createGame() {
    await action(async () => {
      const data = await request('/api/games', { body: mode === 'computer' ? { mode, difficulty } : { mode } });
      const token = data.invitation_url ? new URL(data.invitation_url).pathname.split('/').pop() : null;
      const url = token ? `${window.location.origin}/?invite=${encodeURIComponent(token)}` : null;
      window.history.replaceState(null, '', window.location.pathname);
      saveSession(data, token, url);
      setInviteLoading(false);
    });
  }

  async function joinGame() {
    await action(async () => {
      const data = await request(`/api/invitations/${encodeURIComponent(invitation)}/join`, { body: {} });
      saveSession(data, invitation, null);
    });
  }

  async function move(cell) {
    if (!canMove || game.board[cell]) return;
    await action(async () => {
      const state = await request(`/api/games/${session.game_id}/moves`, {
        token: session.player_token, body: { cell },
      });
      setGame(state);
    });
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(session.invitation_url);
      setCopied(true);
    } catch {
      setError({ key: 'copyError' });
    }
  }

  const notebook = theme === 'grid' || theme === 'ruled';
  const canMove = Boolean(session && game?.status === 'playing' && game.turn === session.your_mark && !pending);
  const board = game?.board || EMPTY_BOARD;
  let status = 'startStatus';
  if (inviteLoading) status = 'checking';
  else if (game?.status === 'waiting') status = session ? 'waiting' : 'invited';
  else if (game?.status === 'playing') status = !session ? 'full' : game.turn === session.your_mark ? 'yourTurn' : 'theirTurn';
  else if (game?.status === 'draw') status = 'draw';
  else if (game?.status === 'won') status = !session ? (game.winner === 'X' ? 'wonX' : 'wonO') : game.winner === session.your_mark ? 'won' : 'lost';
  const activeGame = game && ['waiting', 'playing'].includes(game.status) && session;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label={t('home')}><span className="brand-icon" aria-hidden="true">× ○</span></a>
        <div className="appearance" aria-label={t('appearance')}>
          <div className="segmented">
            <button aria-pressed={!notebook} onClick={() => setTheme('light')}>{t('ordinary')}</button>
            <button aria-pressed={notebook} onClick={() => setTheme('grid')}>{t('notebook')}</button>
          </div>
          <button className="theme-toggle" onClick={() => setTheme(notebook ? (theme === 'grid' ? 'ruled' : 'grid') : (theme === 'light' ? 'dark' : 'light'))}>
            {notebook ? (theme === 'grid' ? `☰ ${t('ruled')}` : `▦ ${t('grid')}`) : (theme === 'light' ? `☾ ${t('dark')}` : `☀ ${t('light')}`)}
          </button>
          <details className="language-picker">
            <summary aria-label={t('language')}>{t('language')} ▾</summary>
            <div className="language-list" role="group" aria-label={t('language')}>
              {languages.map((code) => <button key={code} aria-pressed={language === code} onClick={(event) => {
                setLanguage(code);
                const details = event.currentTarget.closest('details');
                details.open = false;
                details.querySelector('summary').focus();
              }}>{translations[language].languageNames[code]}</button>)}
            </div>
          </details>
        </div>
      </header>

      <main className="game-layout">
        <div className="heading"><h1>{t('titleX')} <span>&</span> {t('titleO')}</h1><p>{t('subtitle')}</p></div>
        <section className="game-area" aria-label={t('title')}>
          <div className="players">
            <div className={`player ${game?.turn === 'X' ? 'active' : ''}`}><span className="small-mark x">×</span><span>{t(session?.your_mark === 'O' ? 'opponent' : 'you')}<small>{t('crosses')}</small></span></div>
            <span className="versus">{t('versus')}</span>
            <div className={`player ${game?.turn === 'O' ? 'active' : ''}`}><span className="small-mark o">○</span><span>{t(game?.mode === 'computer' || (!game && mode === 'computer') ? 'computer' : session?.your_mark === 'O' ? 'you' : 'friend')}<small>{t('noughts')}</small></span></div>
          </div>
          <div className="board" role="group" aria-label={t('board')}>
            {board.map((value, cell) => (
              <button key={cell} className={`cell ${game?.winning_cells.includes(cell) ? 'winner' : ''}`} disabled={!canMove || Boolean(value)} onClick={() => move(cell)} aria-label={t('cell', { row: Math.floor(cell / 3) + 1, column: cell % 3 + 1, value: value || t('empty') })}>
                <Mark value={value} />
              </button>
            ))}
          </div>
          <p className="status" role="status"><span className={`status-dot ${canMove ? 'ready' : ''}`} />{t(pending ? 'pending' : status)}</p>
        </section>

        <section className="controls" aria-label={t('settings')}>
          {invitation && !session && game?.status === 'waiting' && <button className="primary" disabled={pending} onClick={joinGame}>{t('join')}</button>}
          <div className="mode-row"><span id="mode-label">{t('chooseMode')}</span><div className="segmented" role="group" aria-labelledby="mode-label"><button aria-pressed={mode === 'computer'} disabled={pending} onClick={() => setMode('computer')}>{t('withComputer')}</button><button aria-pressed={mode === 'friend'} disabled={pending} onClick={() => setMode('friend')}>{t('withFriend')}</button></div></div>
          {mode === 'computer' && <div className="difficulty-row"><label htmlFor="difficulty">{t('difficulty')}</label><select id="difficulty" value={difficulty} disabled={pending} onChange={(event) => setDifficulty(event.target.value)}><option value="easy">{t('easy')}</option><option value="medium">{t('medium')}</option><option value="unbeatable">{t('unbeatable')}</option></select></div>}
          <button className="primary" disabled={pending} onClick={() => {
            if (!activeGame || window.confirm(t('confirmNew'))) createGame();
          }}><span>↻</span> {t(game ? 'newGame' : 'startGame')}</button>
          {session?.invitation_url && <div className="invitation"><label htmlFor="invite-link">{t('inviteLabel')}</label><div className="link-row"><input id="invite-link" readOnly value={session.invitation_url} onFocus={(event) => event.target.select()} /><button onClick={copyLink}>{t(copied ? 'copied' : 'copy')}</button></div><small>{t('newLink')}</small></div>}
          {error && <p className="error" role="alert">{t(error.key, error.values)}</p>}
        </section>
      </main>
      <button ref={rulesButton} className={`rules-toggle ${rulesOpen ? 'is-open' : ''}`} aria-label={t(rulesOpen ? 'closeRules' : 'openRules')} aria-expanded={rulesOpen} aria-controls="game-rules" onClick={() => setRulesOpen(!rulesOpen)}><span aria-hidden="true">{rulesOpen ? '›' : '‹'}</span></button>
      {rulesOpen && <aside className="rules-panel" id="game-rules" aria-labelledby="rules-title">
        <h2 id="rules-title">{t('rules')}</h2><p>{t('rulesIntro')}</p>
        <ol>{translations[language].rulesList.map((rule, index) => <li key={index}>{rule}</li>)}</ol>
      </aside>}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
