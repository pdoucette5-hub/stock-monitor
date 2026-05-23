import React, { useState, useEffect } from 'react';
import axios from 'axios';

// Mock data matching your screenshot so you can see the UI immediately.
// We will replace this with your actual API fetch once the UI looks right!
const MOCK_DATA = [
  { ticker: 'MU', include: true, sharesOwned: 63.45, eligibleShares: 63.45, locked: 0.00, cagr: '59.37%', action: 'Strong Buy', confidence: 'OK' },
  { ticker: 'NVDA', include: true, sharesOwned: 697.74, eligibleShares: 697.74, locked: 0.00, cagr: '49.40%', action: 'Strong Buy', confidence: 'OK' },
  { ticker: 'PLTR', include: true, sharesOwned: 275.44, eligibleShares: 275.44, locked: 0.00, cagr: '41.54%', action: 'Strong Buy', confidence: 'Review Assumptions' },
  { ticker: 'ORCL', include: true, sharesOwned: 140.77, eligibleShares: 140.77, locked: 0.00, cagr: '35.36%', action: 'Strong Buy', confidence: 'OK' },
  { ticker: 'META', include: true, sharesOwned: 124.25, eligibleShares: 124.25, locked: 0.00, cagr: '31.71%', action: 'Strong Buy', confidence: 'OK' },
  { ticker: 'CELH', include: true, sharesOwned: 1554.54, eligibleShares: 1554.54, locked: 0.00, cagr: '28.95%', action: 'Strong Buy', confidence: 'OK' },
  { ticker: 'SOFI', include: true, sharesOwned: 5343.35, eligibleShares: 5343.35, locked: 0.00, cagr: '27.03%', action: 'Buy', confidence: 'OK' },
];

export default function Redistribution() {
  const [rows, setRows] = useState(MOCK_DATA);
  const [loading, setLoading] = useState(false);

  // --- Handlers for the Top Buttons ---
  const handleExcludeAll = () => {
    setRows(rows.map(row => ({ ...row, include: false })));
  };

  const handleIncludeAll = () => {
    setRows(rows.map(row => ({ ...row, include: true })));
  };

  const handleSetEligibleOwned = () => {
    setRows(rows.map(row => ({ ...row, eligibleShares: row.sharesOwned })));
  };

  const handleSave = () => {
    // This is where we will send the PUT request back to your FastAPI server
    console.log("Saving participation data:", rows);
    alert("Check console for saved data!");
  };

  // --- Handlers for Table Inputs ---
  const handleToggleRow = (ticker) => {
    setRows(rows.map(row => 
      row.ticker === ticker ? { ...row, include: !row.include } : row
    ));
  };

  const handleEligibleChange = (ticker, value) => {
    setRows(rows.map(row => 
      row.ticker === ticker ? { ...row, eligibleShares: value } : row
    ));
  };

  // --- Badge Styling Helpers ---
  const getActionBadge = (action) => {
    if (action === 'Strong Buy') return 'bg-emerald-100 text-emerald-800 border-emerald-200';
    if (action === 'Buy') return 'bg-green-50 text-green-700 border-green-200';
    return 'bg-slate-100 text-slate-700 border-slate-200'; // Default
  };

  const getConfidenceBadge = (confidence) => {
    if (confidence === 'OK') return 'text-emerald-600 border-emerald-200';
    if (confidence === 'Review Assumptions') return 'bg-amber-50 text-amber-700 border-amber-200';
    return 'text-slate-600 border-slate-200'; // Default
  };

  const includedCount = rows.filter(r => r.include).length;

  return (
    <div className="space-y-6">
      
      {/* Header Section */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-2">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Redistribution participation</h1>
          <p className="text-sm text-slate-500 mt-1">
            {includedCount} of {rows.length} holdings included in action calculations.
          </p>
        </div>
        
        {/* Action Buttons */}
        <div className="flex flex-wrap items-center gap-2">
          <button onClick={handleExcludeAll} className="px-3 py-1.5 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors">
            Exclude all
          </button>
          <button onClick={handleIncludeAll} className="px-3 py-1.5 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors">
            Include all
          </button>
          <button onClick={handleSetEligibleOwned} className="px-3 py-1.5 text-sm font-medium text-slate-700 bg-white border border-slate-300 rounded-md hover:bg-slate-50 transition-colors">
            Set eligible = owned
          </button>
          <button onClick={handleSave} className="px-4 py-1.5 text-sm font-medium text-white bg-slate-900 rounded-md hover:bg-slate-800 transition-colors shadow-sm ml-2">
            Save participation
          </button>
        </div>
      </div>

      {/* Main Table Card */}
      <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left text-slate-600">
            <thead className="text-xs font-semibold text-slate-500 bg-white border-b border-slate-200">
              <tr>
                <th className="px-6 py-4">Include</th>
                <th className="px-6 py-4">Ticker</th>
                <th className="px-6 py-4">Shares owned</th>
                <th className="px-6 py-4">Eligible shares</th>
                <th className="px-6 py-4">Locked</th>
                <th className="px-6 py-4">Weighted CAGR</th>
                <th className="px-6 py-4">Valuation action</th>
                <th className="px-6 py-4">Confidence</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <tr key={row.ticker} className="hover:bg-slate-50/50 transition-colors">
                  <td className="px-6 py-3">
                    <input 
                      type="checkbox" 
                      checked={row.include} 
                      onChange={() => handleToggleRow(row.ticker)}
                      className="w-4 h-4 text-blue-600 bg-slate-100 border-slate-300 rounded focus:ring-blue-500 cursor-pointer"
                    />
                  </td>
                  <td className="px-6 py-3 font-semibold text-slate-900">{row.ticker}</td>
                  <td className="px-6 py-3 tabular-nums">{row.sharesOwned}</td>
                  <td className="px-6 py-3">
                    <input 
                      type="number" 
                      value={row.eligibleShares}
                      onChange={(e) => handleEligibleChange(row.ticker, e.target.value)}
                      className="w-24 px-2 py-1 text-sm border border-slate-200 rounded text-slate-700 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 tabular-nums"
                    />
                  </td>
                  <td className="px-6 py-3 tabular-nums">{row.locked.toFixed(2)}</td>
                  <td className="px-6 py-3 tabular-nums">{row.cagr}</td>
                  <td className="px-6 py-3">
                    <span className={`px-2.5 py-1 rounded-full text-xs font-medium border ${getActionBadge(row.action)}`}>
                      {row.action}
                    </span>
                  </td>
                  <td className="px-6 py-3">
                    <span className={`px-2.5 py-1 rounded-full text-xs font-medium border ${getConfidenceBadge(row.confidence)}`}>
                      {row.confidence}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}