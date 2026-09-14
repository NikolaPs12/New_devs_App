import React, { useEffect, useState } from "react";
import { RevenueSummary } from "./RevenueSummary";
import { SecureAPI } from "../lib/secureApi";
import { useAuth } from "../contexts/AuthContext.new";

interface Property {
  id: string;
  name: string;
  timezone: string;
}

const DashboardContent: React.FC = () => {
  const [properties, setProperties] = useState<Property[]>([]);
  const [selectedProperty, setSelectedProperty] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [period, setPeriod] = useState('monthly');
  const [reportMonth, setReportMonth] = useState(() => {
    const today = new Date();
    return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
  });
  const [reportYear, setReportYear] = useState(new Date().getFullYear());

  useEffect(() => {
    let active = true;
    SecureAPI.getDashboardProperties()
      .then((items) => {
        if (!active) return;
        setProperties(items);
        setSelectedProperty(items[0]?.id || '');
      })
      .catch(() => {
        if (active) setError('Failed to load properties');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const month = period === 'monthly' ? Number(reportMonth.slice(5, 7)) : undefined;
  const year = period === 'monthly' ? Number(reportMonth.slice(0, 4)) : period === 'annual' ? reportYear : undefined;
  const validPeriod = period === 'all' || (year && year >= 1900 && year <= 9998 && (period === 'annual' || (month && month >= 1 && month <= 12)));
  const property = properties.find((item) => item.id === selectedProperty);

  return (
    <div className="p-4 lg:p-6 min-h-full">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold mb-6 text-gray-900">Property Management Dashboard</h1>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 lg:p-6">
          <h2 className="text-lg lg:text-xl font-medium text-gray-900 mb-2">Revenue Overview</h2>
          <p className="text-sm text-gray-600 mb-6">Revenue by check-in date in each property's local time.</p>
          {loading ? <p>Loading properties...</p> : error ? <p role="alert" className="text-red-500">{error}</p> : properties.length === 0 ? <p>No properties available.</p> : (
            <>
              <div className="flex flex-wrap gap-4 mb-6">
                <div>
                  <label htmlFor="revenue-property" className="block text-xs font-medium text-gray-700 mb-1">Select Property</label>
                  <select id="revenue-property" value={selectedProperty} onChange={(e) => setSelectedProperty(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-md text-sm">
                    {properties.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="revenue-period" className="block text-xs font-medium text-gray-700 mb-1">Report period</label>
                  <select id="revenue-period" value={period} onChange={(e) => setPeriod(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-md text-sm">
                    <option value="monthly">Monthly</option>
                    <option value="annual">Annual</option>
                    <option value="all">All time</option>
                  </select>
                </div>
                {period === 'monthly' && (
                  <div>
                    <label htmlFor="revenue-month" className="block text-xs font-medium text-gray-700 mb-1">Month</label>
                    <input id="revenue-month" type="month" min="1900-01" max="9998-12" value={reportMonth} onChange={(e) => setReportMonth(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-md text-sm" />
                  </div>
                )}
                {period === 'annual' && (
                  <div>
                    <label htmlFor="revenue-year" className="block text-xs font-medium text-gray-700 mb-1">Year</label>
                    <input id="revenue-year" type="number" min="1900" max="9998" value={reportYear || ''} onChange={(e) => setReportYear(Number(e.target.value))} className="px-3 py-2 border border-gray-300 rounded-md text-sm" />
                  </div>
                )}
              </div>
              <p className="text-xs text-gray-500 mb-4">Property time zone: {property?.timezone}</p>
              {validPeriod ? (
                <RevenueSummary key={`${selectedProperty}:${period}:${year}:${month}`} propertyId={selectedProperty} month={month} year={year} />
              ) : <p>Select a valid reporting period.</p>}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

const Dashboard: React.FC = () => {
  const { user } = useAuth();
  return <DashboardContent key={`${user?.id}:${user?.tenant_id}`} />;
};

export default Dashboard;
