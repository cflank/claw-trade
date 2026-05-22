import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { LegacyReportRoute } from '../routes/LegacyReportRoute';

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

describe('legacy report route', () => {
  it('redirects /reports/:reportId back to workspace with query reportId', async () => {
    render(
      <MemoryRouter initialEntries={['/reports/report-2']}>
        <Routes>
          <Route path="/reports/:reportId" element={<LegacyReportRoute />} />
          <Route path="/" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('location')).toHaveTextContent('/?reportId=report-2');
  });
});
