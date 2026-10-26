import { createRoot } from 'react-dom/client';
import { createTheme, MantineProvider, Pagination } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@mantine/core/styles.css';
import '@fontsource-variable/inter';
import 'yet-another-react-lightbox/styles.css';
import 'yet-another-react-lightbox/plugins/counter.css';
import App from './App';
import './styles.css';

const theme = createTheme({
  fontFamily: 'Inter Variable, sans-serif',
  primaryColor: 'blue',
  defaultRadius: 'md',
  headings: { fontWeight: '600' },
  components: { Pagination: Pagination.extend({ defaultProps: {
    getControlProps: control => ({ 'aria-label': `${control} page` }),
  } }) },
  colors: { blue: ['#eef6ff', '#dbeaff', '#b6d3fc', '#8abbfa', '#63a7f8', '#479af8', '#0f68ea', '#0a5ed4', '#0653bd', '#0049a8'] },
});
const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={client}><MantineProvider theme={theme} forceColorScheme="light" cssVariablesResolver={() => ({
    variables: {}, dark: {}, light: {
      '--mantine-color-body': '#f7f7f7',
      '--mantine-color-text': '#222222',
      '--mantine-color-dimmed': '#6a6a6a',
      '--mantine-color-gray-light-color': '#495057',
      '--mantine-color-green-light-color': '#237835',
    },
  })}><App /></MantineProvider></QueryClientProvider>,
);
