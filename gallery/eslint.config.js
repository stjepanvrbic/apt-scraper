import eslint from '@eslint/js';
import globals from 'globals';
import hooks from 'eslint-plugin-react-hooks';
import typescript from 'typescript-eslint';

export default typescript.config(
  eslint.configs.recommended,
  ...typescript.configs.recommended,
  { languageOptions: { globals: { ...globals.browser, ...globals.node } } },
  { files: ['src/**/*.{ts,tsx}'], plugins: { 'react-hooks': hooks }, rules: hooks.configs.recommended.rules },
);
