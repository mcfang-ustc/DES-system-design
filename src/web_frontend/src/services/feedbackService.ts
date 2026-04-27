/**
 * Feedback Service - Handles experimental feedback submission API calls
 */

import api from './api';
import type {
  FeedbackRequest,
  FeedbackAsyncResponse,
  FeedbackStatusResponse
} from '../types';

export const feedbackService = {
  /**
   * Submit experimental feedback for a recommendation (async)
   * POST /api/v1/feedback
   * Returns immediately with processing status
   */
  submitFeedback: async (
    feedbackData: FeedbackRequest
  ): Promise<FeedbackAsyncResponse> => {
    const response = await api.post<FeedbackAsyncResponse>(
      '/api/v1/feedback/',
      feedbackData
    );
    return response.data;
  },

  /**
   * Check feedback processing status
   * GET /api/v1/feedback/{recommendation_id}/status
   */
  checkStatus: async (
    recommendationId: string
  ): Promise<FeedbackStatusResponse> => {
    const response = await api.get<FeedbackStatusResponse>(
      `/api/v1/feedback/${recommendationId}/status`
    );
    return response.data;
  },

  /**
   * Poll feedback processing status until completed or failed
   * @param recommendationId - Recommendation ID
   * @param onProgress - Callback for status updates
   * @param interval - Polling interval in ms (default: 2000)
   * @param timeout - Max polling time in ms (default: 300000 = 5 min)
   */
  pollStatus: async (
    recommendationId: string,
    onProgress?: (status: FeedbackStatusResponse) => void,
    interval: number = 2000,
    timeout: number = 300000
  ): Promise<FeedbackStatusResponse> => {
    const startTime = Date.now();

    return new Promise((resolve, reject) => {
      const poll = async () => {
        try {
          const response = await feedbackService.checkStatus(recommendationId);

          // Call progress callback
          if (onProgress) {
            onProgress(response);
          }

          // Check if completed or failed
          if (response.data.status === 'completed') {
            resolve(response);
            return;
          } else if (response.data.status === 'failed') {
            reject(new Error(response.data.error || 'Processing failed'));
            return;
          }

          // Check timeout
          if (Date.now() - startTime > timeout) {
            reject(new Error('Polling timeout exceeded'));
            return;
          }

          // Schedule next poll
          setTimeout(poll, interval);
        } catch (error) {
          reject(error);
        }
      };

      // Start polling
      poll();
    });
  },
};
