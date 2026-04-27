/**
 * Task Service - Handles task creation API calls
 */

import api from './api';
import type { TaskRequest, TaskResponse } from '../types';

export const taskService = {
  /**
   * Create a new task and get DES formulation recommendations
   * POST /api/v1/tasks
   */
  createTask: async (taskData: TaskRequest): Promise<TaskResponse> => {
    const response = await api.post<TaskResponse>('/api/v1/tasks/', taskData);
    return response.data;
  },
};
