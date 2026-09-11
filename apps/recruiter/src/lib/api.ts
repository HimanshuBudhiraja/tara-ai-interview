/** Raised with a message already fit to show a recruiter. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /**
     * The response body's `detail` when it was structured rather than a string.
     *
     * The result endpoint answers 409 two different ways — "there is no result
     * yet, here is the status" and "the result contradicted itself, here is
     * why" — and a report that can only read the message cannot tell them apart.
     */
    readonly detail?: unknown,
  ) {
    super(message);
  }
}
