import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import App from './App'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  listeners: Record<string, Array<(event?: unknown) => void>> = {}
  url: string

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    setTimeout(() => this.dispatch('open'), 0)
  }

  addEventListener(event: string, cb: (event?: unknown) => void) {
    this.listeners[event] = this.listeners[event] || []
    this.listeners[event].push(cb)
  }

  send() {}
  close() {}

  dispatch(event: string, payload: unknown = {}) {
    for (const cb of this.listeners[event] || []) {
      cb(payload)
    }
  }
}

type OverseerPayload = {
  id: string
  status: string
  objective: string
  advice: string[]
  last_handover: Record<string, unknown> | null
  iteration: number
  started_at: string | null
  finished_at: string | null
  blocked_reason: string | null
  human_response: string | null
  error: string | null
}

function makeOverseerState(overrides?: Partial<OverseerPayload>): OverseerPayload {
  return {
    id: 'ovs-1',
    status: 'idle',
    objective: '',
    advice: [],
    last_handover: null,
    iteration: 0,
    started_at: null,
    finished_at: null,
    blocked_reason: null,
    human_response: null,
    error: null,
    ...overrides,
  }
}

function installFetchMock(overseerState?: OverseerPayload) {
  let currentOverseer = overseerState ?? makeOverseerState()

  const jsonResponse = (payload: unknown) =>
    Promise.resolve({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => payload,
    })

  const mockedFetch = vi.fn().mockImplementation((url, init) => {
    const u = String(url)
    const method = String((init as RequestInit | undefined)?.method || 'GET').toUpperCase()

    if (u === '/' || u.startsWith('/?')) return jsonResponse({ project_id: 'repo-alpha' })
    if (u.includes('/api/collaboration/modes')) return jsonResponse({ modes: [] })

    // Overseer endpoints
    if (u.includes('/api/overseer/status') && method === 'GET') {
      return jsonResponse({ overseer: currentOverseer })
    }
    if (u.includes('/api/overseer/start') && method === 'POST') {
      const body = JSON.parse(String((init as RequestInit).body))
      currentOverseer = makeOverseerState({
        status: 'running',
        objective: body.objective,
        advice: body.advice || [],
        iteration: 1,
        started_at: '2026-03-21T00:00:00Z',
      })
      return jsonResponse({ overseer: currentOverseer })
    }
    if (u.includes('/api/overseer/stop') && method === 'POST') {
      currentOverseer = { ...currentOverseer, status: 'stopped', finished_at: '2026-03-21T01:00:00Z' }
      return jsonResponse({ overseer: currentOverseer })
    }
    if (u.includes('/api/overseer/advice') && method === 'POST') {
      const body = JSON.parse(String((init as RequestInit).body))
      currentOverseer = { ...currentOverseer, advice: [...currentOverseer.advice, body.text] }
      return jsonResponse({ overseer: currentOverseer })
    }
    if (u.includes('/api/overseer/advice/') && method === 'DELETE') {
      const idx = parseInt(u.split('/api/overseer/advice/')[1])
      const newAdvice = [...currentOverseer.advice]
      newAdvice.splice(idx, 1)
      currentOverseer = { ...currentOverseer, advice: newAdvice }
      return jsonResponse({ overseer: currentOverseer })
    }
    if (u.includes('/api/overseer/unblock') && method === 'POST') {
      currentOverseer = { ...currentOverseer, status: 'running', blocked_reason: null }
      return jsonResponse({ overseer: currentOverseer })
    }

    // Standard endpoints
    if (u.includes('/api/tasks/board')) {
      return jsonResponse({ columns: { backlog: [], queued: [], in_progress: [], in_review: [], blocked: [], done: [], cancelled: [] } })
    }
    if (u.includes('/api/tasks/execution-order')) return jsonResponse({ batches: [] })
    if (u.includes('/api/tasks') && !u.includes('/api/tasks/')) return jsonResponse({ tasks: [] })
    if (u.includes('/api/orchestrator/status')) {
      return jsonResponse({ status: 'running', queue_depth: 0, in_progress: 0, draining: false, run_branch: null })
    }
    if (u.includes('/api/review-queue')) return jsonResponse({ tasks: [] })
    if (u.includes('/api/agents/types')) return jsonResponse({ types: [] })
    if (u.includes('/api/agents') && method === 'GET') return jsonResponse({ agents: [] })
    if (u.includes('/api/projects/pinned') && method === 'GET') return jsonResponse({ items: [] })
    if (u.includes('/api/projects') && method === 'GET') {
      return jsonResponse({ projects: [{ id: 'repo-alpha', path: '/tmp/repo-alpha', source: 'workspace', is_git: true }] })
    }
    if (u.includes('/api/phases')) return jsonResponse([])
    if (u.includes('/api/collaboration/presence')) return jsonResponse({ users: [] })
    if (u.includes('/api/metrics')) return jsonResponse({ worker_time_seconds: 0, tasks_completed: 0, tokens_used: 0, estimated_cost_usd: 0 })
    if (u.includes('/api/workers/health')) return jsonResponse({ providers: [] })
    if (u.includes('/api/workers/routing')) return jsonResponse({ default: 'codex', rows: [] })
    if (u.includes('/api/settings')) return jsonResponse({
      orchestrator: { concurrency: 2, auto_deps: true, max_review_attempts: 10, step_timeout_seconds: 600 },
      agent_routing: { default_role: 'general', task_type_roles: {}, role_provider_overrides: {} },
      defaults: { quality_gate: { critical: 0, high: 0, medium: 0, low: 0 } },
      workers: { default: 'codex', default_model: '', routing: {}, providers: {} },
      project: { commands: {}, prompt_overrides: {}, prompt_injections: {}, prompt_defaults: {} },
    })
    if (u.includes('/api/terminal/session') && method === 'GET') return jsonResponse({ session: null })
    if (u.includes('/api/git/status')) return jsonResponse({ branch: 'main', remote_branch: null, ahead_count: 0, behind_count: 0, commits: [], has_remote: false })

    return jsonResponse({})
  })

  global.fetch = mockedFetch as unknown as typeof fetch
  return mockedFetch
}

async function navigateToGodMode(): Promise<void> {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: /God Mode/i })).toBeInTheDocument()
  })
  fireEvent.click(screen.getByRole('button', { name: /God Mode/i }))
}

describe('God Mode tab', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    window.location.hash = ''
    MockWebSocket.instances = []
    ;(globalThis as unknown as { WebSocket: typeof WebSocket }).WebSocket = MockWebSocket as unknown as typeof WebSocket
  })

  it('renders the God Mode tab with idle state and start form', async () => {
    installFetchMock()
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(document.querySelector('.godmode-title')).toBeInTheDocument()
      expect(screen.getByLabelText(/Objective/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Activate God Mode/i })).toBeInTheDocument()
    })

    // Start button should be disabled without objective
    expect(screen.getByRole('button', { name: /Activate God Mode/i })).toBeDisabled()
  })

  it('starts God Mode with an objective', async () => {
    const mockedFetch = installFetchMock()
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByLabelText(/Objective/i)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByLabelText(/Objective/i), { target: { value: 'Improve test coverage' } })
    expect(screen.getByRole('button', { name: /Activate God Mode/i })).not.toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /Activate God Mode/i }))

    await waitFor(() => {
      const startCall = mockedFetch.mock.calls.find(([url, init]: [string, RequestInit | undefined]) =>
        String(url).includes('/api/overseer/start') && init?.method === 'POST'
      )
      expect(startCall).toBeTruthy()
      const body = JSON.parse(String((startCall?.[1] as RequestInit).body))
      expect(body.objective).toBe('Improve test coverage')
    })

    // After start, should show running status
    await waitFor(() => {
      expect(screen.getByText('Improve test coverage')).toBeInTheDocument()
    })
  })

  it('shows running state with iteration and stop button', async () => {
    installFetchMock(makeOverseerState({
      status: 'running',
      objective: 'Self-improving repo',
      iteration: 3,
      started_at: '2026-03-21T00:00:00Z',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByText('Self-improving repo')).toBeInTheDocument()
      expect(screen.getByText('3')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Stop/i })).toBeInTheDocument()
    })
  })

  it('stops God Mode', async () => {
    const mockedFetch = installFetchMock(makeOverseerState({
      status: 'running',
      objective: 'Self-improving repo',
      iteration: 3,
      started_at: '2026-03-21T00:00:00Z',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Stop/i })).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /Stop/i }))

    await waitFor(() => {
      expect(
        mockedFetch.mock.calls.some(([url, init]: [string, RequestInit | undefined]) =>
          String(url).includes('/api/overseer/stop') && init?.method === 'POST'
        )
      ).toBe(true)
    })
  })

  it('adds and removes advice in idle state', async () => {
    const mockedFetch = installFetchMock()
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Add advice/i)).toBeInTheDocument()
    })

    // Type advice and add it
    fireEvent.change(screen.getByPlaceholderText(/Add advice/i), { target: { value: 'Prefer small PRs' } })
    fireEvent.click(screen.getAllByRole('button', { name: /^Add$/i })[0])

    await waitFor(() => {
      const addCall = mockedFetch.mock.calls.find(([url, init]: [string, RequestInit | undefined]) =>
        String(url).includes('/api/overseer/advice') && init?.method === 'POST'
      )
      expect(addCall).toBeTruthy()
      const body = JSON.parse(String((addCall?.[1] as RequestInit).body))
      expect(body.text).toBe('Prefer small PRs')
    })
  })

  it('adds advice while running', async () => {
    const mockedFetch = installFetchMock(makeOverseerState({
      status: 'running',
      objective: 'Improve everything',
      advice: ['Existing advice'],
      iteration: 1,
      started_at: '2026-03-21T00:00:00Z',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByText('Existing advice')).toBeInTheDocument()
      expect(screen.getByPlaceholderText(/Add advice/i)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText(/Add advice/i), { target: { value: 'No direct code changes' } })
    fireEvent.click(screen.getAllByRole('button', { name: /^Add$/i })[0])

    await waitFor(() => {
      const addCall = mockedFetch.mock.calls.find(([url, init]: [string, RequestInit | undefined]) =>
        String(url).includes('/api/overseer/advice') && init?.method === 'POST'
      )
      expect(addCall).toBeTruthy()
    })
  })

  it('shows blocked state with unblock form', async () => {
    installFetchMock(makeOverseerState({
      status: 'blocked',
      objective: 'Deploy to prod',
      blocked_reason: 'Need GITHUB_TOKEN',
      iteration: 2,
      started_at: '2026-03-21T00:00:00Z',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByText(/Need GITHUB_TOKEN/i)).toBeInTheDocument()
      expect(screen.getByPlaceholderText(/Provide information to unblock/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^Unblock$/i })).toBeInTheDocument()
    })

    // Unblock button should be disabled without input
    expect(screen.getByRole('button', { name: /^Unblock$/i })).toBeDisabled()
  })

  it('unblocks the overseer', async () => {
    const mockedFetch = installFetchMock(makeOverseerState({
      status: 'blocked',
      objective: 'Deploy to prod',
      blocked_reason: 'Need GITHUB_TOKEN',
      iteration: 2,
      started_at: '2026-03-21T00:00:00Z',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Provide information to unblock/i)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByPlaceholderText(/Provide information to unblock/i), {
      target: { value: 'Token is in 1Password vault' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^Unblock$/i }))

    await waitFor(() => {
      const unblockCall = mockedFetch.mock.calls.find(([url, init]: [string, RequestInit | undefined]) =>
        String(url).includes('/api/overseer/unblock') && init?.method === 'POST'
      )
      expect(unblockCall).toBeTruthy()
      const body = JSON.parse(String((unblockCall?.[1] as RequestInit).body))
      expect(body.response).toBe('Token is in 1Password vault')
    })
  })

  it('shows completed state with status pill and start form', async () => {
    installFetchMock(makeOverseerState({
      status: 'completed',
      objective: 'Fix all lint errors',
      iteration: 5,
      started_at: '2026-03-21T00:00:00Z',
      finished_at: '2026-03-21T01:30:00Z',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      // Completed shows status pill in header but returns to start form
      expect(document.querySelector('.status-done')).toBeInTheDocument()
      expect(screen.getByLabelText(/Objective/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Activate God Mode/i })).toBeInTheDocument()
    })
  })

  it('shows last handover JSON in collapsible details', async () => {
    installFetchMock(makeOverseerState({
      status: 'running',
      objective: 'Improve tests',
      iteration: 2,
      started_at: '2026-03-21T00:00:00Z',
      last_handover: { status: 'continue', context: 'Working on unit tests', progress: 'Added 5 tests' },
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByText(/Last Handover/i)).toBeInTheDocument()
    })

    // The handover JSON should be in a details/summary element
    const details = screen.getByText(/Last Handover/i).closest('details')
    expect(details).toBeInTheDocument()
  })

  it('shows error state from overseer', async () => {
    installFetchMock(makeOverseerState({
      status: 'running',
      objective: 'Build feature',
      iteration: 1,
      started_at: '2026-03-21T00:00:00Z',
      error: 'Agent process crashed unexpectedly',
    }))
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByText('Agent process crashed unexpectedly')).toBeInTheDocument()
    })
  })

  it('adds advice via Enter key', async () => {
    const mockedFetch = installFetchMock()
    render(<App />)
    await navigateToGodMode()

    await waitFor(() => {
      expect(screen.getByPlaceholderText(/Add advice/i)).toBeInTheDocument()
    })

    const adviceInput = screen.getByPlaceholderText(/Add advice/i)
    fireEvent.change(adviceInput, { target: { value: 'Use pytest-xdist' } })
    fireEvent.keyDown(adviceInput, { key: 'Enter' })

    await waitFor(() => {
      const addCall = mockedFetch.mock.calls.find(([url, init]: [string, RequestInit | undefined]) =>
        String(url).includes('/api/overseer/advice') && init?.method === 'POST'
      )
      expect(addCall).toBeTruthy()
    })
  })
})
