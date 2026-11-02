import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Test helper: clicks the "N unsaved changes" button named `count` and returns the change list's rows, one
// [field, saved, edited] triple per edited field, in the order the list shows them.
export async function unsavedRows(count: string): Promise<(string | null)[][]> {
  await userEvent.click(screen.getByRole('button', { name: count }))
  const table = screen.getByRole('table', { name: 'Unsaved changes' })
  return within(table).getAllByRole('row').slice(1).map((r) => [...r.querySelectorAll('th, td')].map((c) => c.textContent))
}
