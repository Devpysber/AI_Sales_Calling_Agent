// Applied before first paint so the page never flashes light in dark mode. External file: the CSP allows no inline scripts.
try { if (localStorage.theme === 'dark' || (!localStorage.theme && matchMedia('(prefers-color-scheme: dark)').matches)) document.documentElement.classList.add('dark') } catch {}
