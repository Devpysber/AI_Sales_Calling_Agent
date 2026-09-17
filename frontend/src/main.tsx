
// A deploy replaces the hashed chunks an already-open tab still points at, so a lazy import can 404.
// One reload picks up the new index.html; reloading again would only loop if the chunk is truly gone.
window.addEventListener('error', (e) => {
  if (e.message && e.message.includes('Failed to fetch dynamically imported module')) {
    if (sessionStorage.getItem('chunk-reloaded')) return
    try { sessionStorage.setItem('chunk-reloaded', '1') } catch { /* storage unavailable */ }
    window.location.reload();
  }
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { Toaster } from 'sonner'
import App from './App'
import { ConfirmProvider } from './components/ui'
import { ApiError } from './lib/api'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: true,
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ConfirmProvider>
          <App />
          <Toaster position="bottom-right" richColors closeButton theme="system" />
        </ConfirmProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
