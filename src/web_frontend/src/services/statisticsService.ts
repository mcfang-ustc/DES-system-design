/**
 * Statistics Service - Handles system statistics API calls
 */

import api from './api';
import type { StatisticsResponse, PerformanceTrendResponse } from '../types';

export interface PerformanceTrendParams {
  start_date: string; // ISO format: YYYY-MM-DD
  end_date: string; // ISO format: YYYY-MM-DD
}

export const statisticsService = {
  /**
   * Get comprehensive system statistics
   * GET /api/v1/statistics
   */
  getStatistics: async (): Promise<StatisticsResponse> => {
    const response = await api.get<StatisticsResponse>('/api/v1/statistics/');
    return response.data;
  },

  /**
   * Get performance trend for a specific date range
   * GET /api/v1/statistics/performance-trend
   */
  getPerformanceTrend: async (
    params: PerformanceTrendParams
  ): Promise<PerformanceTrendResponse> => {
    const response = await api.get<PerformanceTrendResponse>(
      '/api/v1/statistics/performance-trend',
      { params }
    );
    return response.data;
  },
};
