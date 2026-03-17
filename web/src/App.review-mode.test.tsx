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

function installFetchMock() {
  const jsonResponse = (payload: unknown) =>
    Promise.resolve({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: async () => payload,
    })

  const prList = [
    { number: 42, title: 'Add login page', author: 'alice', head_ref: 'feat/login', base_ref: 'main', url: 'https://github.com/org/repo/pull/42', has_review_task: false, review_task_id: null },
    { number: 43, title: 'Fix footer', author: 'bob', head_ref: 'fix/footer', base_ref: 'main', url: 'https://github.com/org/repo/pull/43', has_review_task: false, review_task_id: null },
  ]

  const mockedFetch = vi.fn().mockImplementation((url: string) => {
    const u = String(url)
    if (u === '/' || u.startsWith('/?')) return jsonResponse({ project_id: 'repo-alpha' })
    if (u.includes('/api/collaboration/modes')) return jsonResponse({ modes: [] })
    if (u.includes('/api/pull-requests') && !u.includes('/review')) return jsonResponse({ items: prList, platform: 'github' })
    if (u.includes('/api/pull-requests') && u.includes('/review')) return jsonResponse({ task_id: 'task-review-1' })
    if (u.includes('/api/tasks/board')) return jsonResponse({ columns: { backlog: [], queued: [], in_progress: [], in_review: [], blocked: [], done: [] } })
    if (u.includes('/api/tasks/execution-order')) return jsonResponse({ batches: [], completed: [] })
    if (u.includes('/api/tasks') && !u.includes('/api/tasks/')) return jsonResponse({ tasks: [] })
    if (u.includes('/api/orchestrator/status')) return jsonResponse({ status: 'running', queue_depth: 0, in_progress: 0, draining: false, run_branch: null })
    if (u.includes('/api/review-queue')) return jsonResponse({ tasks: [] })
    if (u.includes('/api/agents/types')) return jsonResponse({ types: [] })
    if (u.includes('/api/agents')) return jsonResponse({ agents: [] })
    if (u.includes('/api/projects/pinned')) return jsonResponse({ items: [] })
    if (u.includes('/api/projects')) return jsonResponse({ projects: [] })
    if (u.includes('/api/phases')) return jsonResponse([])
    if (u.includes('/api/collaboration/presence')) return jsonResponse({ users: [] })
    if (u.includes('/api/metrics')) return jsonResponse({})
    if (u.includes('/api/settings')) return jsonResponse({
      orchestrator: { concurrency: 2, auto_deps: true, max_review_attempts: 10, step_timeout_seconds: 600 },
      agent_routing: { default_role: 'general', task_type_roles: {}, role_provider_overrides: {} },
      defaults: { quality_gate: { critical: 0, high: 0, medium: 0, low: 0 } },
      workers: { default: 'codex', default_model: '', routing: {}, providers: {} },
      project: { commands: {}, prompt_overrides: {}, prompt_injections: {}, prompt_defaults: {} },
    })
    if (u.includes('/api/workers/health')) return jsonResponse({ providers: [] })
    if (u.includes('/api/workers/routing')) return jsonResponse({ default: 'codex', rows: [] })
    if (u.includes('/api/terminal/session')) return jsonResponse({ session: null })
    return jsonResponse({})
  })

  global.fetch = mockedFetch as unknown as typeof fetch
  return mockedFetch
}

async function openReviewTab() {
  render(<App />)
  await waitFor(() => expect(screen.getAllByRole('button', { name: /^Create Work$/i }).length).toBeGreaterThan(0))
  fireEvent.click(screen.getAllByRole('button', { name: /^Create Work$/i })[0])
  await waitFor(() => expect(screen.getByText('Review PR/MR')).toBeInTheDocument())
  fireEvent.click(screen.getByText('Review PR/MR'))
  await waitFor(() => expect(screen.getByText('Review mode')).toBeInTheDocument())
}

describe('Review mode selector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    window.location.hash = ''
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
    installFetchMock()
  })

  it('renders radio group with Fix Code selected by default', async () => {
    await openReviewTab()
    const fixCodeRadio = screen.getByDisplayValue('fix_only') as HTMLInputElement
    expect(fixCodeRadio.checked).toBe(true)
    expect(screen.getByText('Review & Comment')).toBeInTheDocument()
    expect(screen.getByText('Summarize')).toBeInTheDocument()
    expect(screen.getByText('Fix Code')).toBeInTheDocument()
    expect(screen.getByText('Fix & Respond to Comments')).toBeInTheDocument()
  })

  it('shows review decision dropdown when Review & Comment is selected', async () => {
    await openReviewTab()
    expect(screen.queryByLabelText('Review decision')).toBeNull()
    fireEvent.click(screen.getByDisplayValue('review_comment'))
    expect(screen.getByLabelText('Review decision')).toBeInTheDocument()
    expect(screen.getByText('Comment only')).toBeInTheDocument()
    expect(screen.getByText('Approve')).toBeInTheDocument()
    expect(screen.getByText('Request changes')).toBeInTheDocument()
  })

  it('hides review decision dropdown when Summarize is selected', async () => {
    await openReviewTab()
    fireEvent.click(screen.getByDisplayValue('review_comment'))
    expect(screen.getByLabelText('Review decision')).toBeInTheDocument()
    fireEvent.click(screen.getByDisplayValue('summarize'))
    expect(screen.queryByLabelText('Review decision')).toBeNull()
  })

  it('shows Post comments checkbox for review_comment and fix_respond, hidden for others', async () => {
    await openReviewTab()
    // fix_only by default — no checkbox
    expect(screen.queryByText('Post comments to PR/MR')).toBeNull()

    // review_comment — checkbox visible
    fireEvent.click(screen.getByDisplayValue('review_comment'))
    expect(screen.getByText('Post comments to PR/MR')).toBeInTheDocument()

    // summarize — no checkbox
    fireEvent.click(screen.getByDisplayValue('summarize'))
    expect(screen.queryByText('Post comments to PR/MR')).toBeNull()

    // fix_respond — checkbox visible
    fireEvent.click(screen.getByDisplayValue('fix_respond'))
    expect(screen.getByText('Post comments to PR/MR')).toBeInTheDocument()
  })

  it('resets decision to default when switching away from review_comment', async () => {
    await openReviewTab()
    fireEvent.click(screen.getByDisplayValue('review_comment'))
    const dropdown = screen.getByLabelText('Review decision') as HTMLSelectElement
    fireEvent.change(dropdown, { target: { value: 'approve' } })
    expect(dropdown.value).toBe('approve')

    // Switch away and back
    fireEvent.click(screen.getByDisplayValue('fix_only'))
    fireEvent.click(screen.getByDisplayValue('review_comment'))
    const dropdownAfter = screen.getByLabelText('Review decision') as HTMLSelectElement
    expect(dropdownAfter.value).toBe('comment')
  })

  it('sends review_mode and review_decision in the API call', async () => {
    const mockedFetch = installFetchMock()
    await openReviewTab()

    // Select PR
    await waitFor(() => expect(screen.getByText('#42')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#42').closest('button')!)

    // Select Review & Comment mode and Approve decision
    fireEvent.click(screen.getByDisplayValue('review_comment'))
    const dropdown = screen.getByLabelText('Review decision') as HTMLSelectElement
    fireEvent.change(dropdown, { target: { value: 'approve' } })

    // Submit
    fireEvent.click(screen.getByText('Create Review'))
    await waitFor(() => {
      const reviewCall = mockedFetch.mock.calls.find(
        ([u, init]: [string, RequestInit]) => String(u).includes('/api/pull-requests/42/review') && init?.method === 'POST',
      )
      expect(reviewCall).toBeDefined()
      const body = JSON.parse(String(reviewCall![1].body))
      expect(body.review_mode).toBe('review_comment')
      expect(body.review_decision).toBe('approve')
    })
  })

  it('does not send review_decision when mode is not review_comment', async () => {
    const mockedFetch = installFetchMock()
    await openReviewTab()

    await waitFor(() => expect(screen.getByText('#42')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#42').closest('button')!)

    // Default mode is fix_only — submit directly
    fireEvent.click(screen.getByText('Create Review'))
    await waitFor(() => {
      const reviewCall = mockedFetch.mock.calls.find(
        ([u, init]: [string, RequestInit]) => String(u).includes('/api/pull-requests/42/review') && init?.method === 'POST',
      )
      expect(reviewCall).toBeDefined()
      const body = JSON.parse(String(reviewCall![1].body))
      expect(body.review_mode).toBe('fix_only')
      expect(body.review_decision).toBeUndefined()
    })
  })
})
