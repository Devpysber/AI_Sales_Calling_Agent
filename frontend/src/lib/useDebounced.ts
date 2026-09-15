import { useEffect, useState } from 'react'

/** The value after the user stops typing for `ms`: one request per search, not one per keystroke. */
export function useDebounced<T>(value: T, ms = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return debounced
}
