import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './styles/base.css';
import './styles/layout.css';
import './styles/content.css';
import './styles/login.css';
import './styles/selection.css';
import './styles/publishing.css';

const root = document.getElementById('root');
if (!root) throw new Error('Missing application root');
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: true } },
});
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
