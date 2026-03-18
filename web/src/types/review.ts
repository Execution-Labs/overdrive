export enum ReviewMode {
  ReviewComment = 'review_comment',
  Summarize = 'summarize',
  FixOnly = 'fix_only',
  FixRespond = 'fix_respond',
}

export enum ReviewDecisionType {
  Comment = 'comment',
  Approve = 'approve',
  RequestChanges = 'request_changes',
}

export interface ReviewModeOption {
  value: ReviewMode
  label: string
  description: string
}

export const REVIEW_MODE_OPTIONS: ReviewModeOption[] = [
  { value: ReviewMode.ReviewComment, label: 'Review & Comment', description: 'Post review comments without making code changes' },
  { value: ReviewMode.Summarize, label: 'Summarize', description: 'Generate a summary of changes without posting' },
  { value: ReviewMode.FixOnly, label: 'Fix Code', description: 'Analyze and fix code issues' },
  { value: ReviewMode.FixRespond, label: 'Fix & Respond to Comments', description: 'Fix code and respond to existing review comments' },
]

/** Human-readable labels for each review mode, used in badges. */
export const REVIEW_MODE_LABELS: Record<ReviewMode, string> = {
  [ReviewMode.ReviewComment]: 'Review & Comment',
  [ReviewMode.Summarize]: 'Summarize',
  [ReviewMode.FixOnly]: 'Fix Code',
  [ReviewMode.FixRespond]: 'Fix & Respond',
}

/** Modes that post comments to the PR/MR platform */
export const COMMENT_POSTING_MODES = new Set<ReviewMode>([
  ReviewMode.ReviewComment,
  ReviewMode.FixRespond,
])

export const REVIEW_DECISION_OPTIONS: { value: ReviewDecisionType; label: string }[] = [
  { value: ReviewDecisionType.Comment, label: 'Comment only' },
  { value: ReviewDecisionType.Approve, label: 'Approve' },
  { value: ReviewDecisionType.RequestChanges, label: 'Request changes' },
]
