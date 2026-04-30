export class TroveError extends Error {
  constructor(message: string, public readonly statusCode?: number) {
    super(message)
    this.name = 'TroveError'
  }
}
