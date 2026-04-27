/**
 * Memory Service - Handles memory management API calls
 */

import api from './api';
import type {
  MemoryListResponse,
  MemoryDetailResponse,
  MemoryCreateResponse,
  MemoryUpdateResponse,
  MemoryDeleteResponse,
  MemoryItemCreate,
  MemoryItemUpdate,
} from '../types';

export interface MemoryListParams {
  page?: number;
  page_size?: number;
  is_from_success?: boolean;
  source_task_id?: string;
}

export const memoryService = {
  /**
   * Get list of memories with optional filters and pagination
   * GET /api/v1/memories
   */
  listMemories: async (
    params?: MemoryListParams
  ): Promise<MemoryListResponse> => {
    const response = await api.get<MemoryListResponse>(
      '/api/v1/memories/',
      { params }
    );
    return response.data;
  },

  /**
   * Get detailed information for a specific memory
   * GET /api/v1/memories/{title}
   */
  getMemory: async (title: string): Promise<MemoryDetailResponse> => {
    const response = await api.get<MemoryDetailResponse>(
      `/api/v1/memories/${encodeURIComponent(title)}`
    );
    return response.data;
  },

  /**
   * Create a new memory item
   * POST /api/v1/memories
   */
  createMemory: async (
    memoryData: MemoryItemCreate
  ): Promise<MemoryCreateResponse> => {
    const response = await api.post<MemoryCreateResponse>(
      '/api/v1/memories/',
      memoryData
    );
    return response.data;
  },

  /**
   * Update an existing memory
   * PUT /api/v1/memories/{title}
   */
  updateMemory: async (
    title: string,
    updateData: MemoryItemUpdate
  ): Promise<MemoryUpdateResponse> => {
    const response = await api.put<MemoryUpdateResponse>(
      `/api/v1/memories/${encodeURIComponent(title)}`,
      updateData
    );
    return response.data;
  },

  /**
   * Delete a memory by title
   * DELETE /api/v1/memories/{title}
   */
  deleteMemory: async (title: string): Promise<MemoryDeleteResponse> => {
    const response = await api.delete<MemoryDeleteResponse>(
      `/api/v1/memories/${encodeURIComponent(title)}`
    );
    return response.data;
  },
};
