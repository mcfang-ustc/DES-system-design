/**
 * Recommendation Service - Handles recommendation management API calls
 */

import api from './api';
import type {
  RecommendationListResponse,
  RecommendationDetailResponse,
  CancelRecommendationResponse,
} from '../types';

export interface RecommendationListParams {
  status?: 'GENERATING' | 'PENDING' | 'COMPLETED' | 'CANCELLED' | 'FAILED';
  material?: string;
  page?: number;
  page_size?: number;
}

export interface RecommendationStatistics {
  all: number;
  GENERATING: number;
  PENDING: number;
  COMPLETED: number;
  FAILED: number;
  CANCELLED: number;
}

export interface StatisticsResponse {
  status: string;
  data: RecommendationStatistics;
}

export const recommendationService = {
  /**
   * Get list of recommendations with optional filters and pagination
   * GET /api/v1/recommendations
   */
  listRecommendations: async (
    params?: RecommendationListParams
  ): Promise<RecommendationListResponse> => {
    const response = await api.get<RecommendationListResponse>(
      '/api/v1/recommendations/',
      { params }
    );
    return response.data;
  },

  /**
   * Get detailed information for a specific recommendation
   * GET /api/v1/recommendations/{id}
   */
  getRecommendationDetail: async (
    recommendationId: string
  ): Promise<RecommendationDetailResponse> => {
    const response = await api.get<RecommendationDetailResponse>(
      `/api/v1/recommendations/${recommendationId}`
    );
    return response.data;
  },

  /**
   * Cancel a pending recommendation
   * PATCH /api/v1/recommendations/{id}/cancel
   */
  cancelRecommendation: async (
    recommendationId: string
  ): Promise<CancelRecommendationResponse> => {
    const response = await api.patch<CancelRecommendationResponse>(
      `/api/v1/recommendations/${recommendationId}/cancel`
    );
    return response.data;
  },

  /**
   * Get recommendation statistics (fast - index only)
   * GET /api/v1/recommendations/statistics
   */
  getStatistics: async (params?: {
    material?: string;
  }): Promise<StatisticsResponse> => {
    const response = await api.get<StatisticsResponse>(
      '/api/v1/recommendations/statistics',
      { params }
    );
    return response.data;
  },
};
