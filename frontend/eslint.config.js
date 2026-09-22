/**
 * This project lints with oxlint (`npm run lint`), not ESLint.
 *
 * Editors that run ESLint on their own reported "ESLint couldn't find an eslint.config.* file" on
 * every file in src/, which reads as an error in the code itself. This flat config exists only to
 * answer that lookup: it claims no files, so nothing is double-linted and no ESLint plugins or
 * parsers are needed. Real lint rules belong in oxlint's configuration.
 */
export default [{ ignores: ['**/*'] }]
