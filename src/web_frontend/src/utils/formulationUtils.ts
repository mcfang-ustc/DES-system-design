/**
 * Utility functions for formulation data formatting
 */

import type { FormulationData } from '../types/formulation';

/**
 * Get human-readable formulation string for display
 */
export function getFormulationDisplayString(formulation: FormulationData): string {
  // Binary formulation
  if (formulation.HBD && formulation.HBA) {
    return `${formulation.HBD} : ${formulation.HBA} (${formulation.molar_ratio.replace(/:/g, ' : ')})`;
  }

  // Multi-component formulation
  if (formulation.components && formulation.components.length > 0) {
    const componentNames = formulation.components.map(c => c.name).join(' + ');
    return `${componentNames} (${formulation.molar_ratio.replace(/:/g, ' : ')})`;
  }

  // Unknown formulation
  return `Unknown formulation (${formulation.molar_ratio.replace(/:/g, ' : ')})`;
}

/**
 * Check if formulation is binary (2 components)
 */
export function isBinaryFormulation(formulation: FormulationData): boolean {
  return !!(formulation.HBD && formulation.HBA);
}

/**
 * Check if formulation is multi-component (>2 components)
 */
export function isMultiComponentFormulation(formulation: FormulationData): boolean {
  return !!(formulation.components && formulation.components.length > 2);
}

/**
 * Get short formulation string for list view (truncate if too long)
 */
export function getShortFormulationString(formulation: FormulationData, maxLength: number = 50): string {
  const fullString = getFormulationDisplayString(formulation);
  if (fullString.length <= maxLength) {
    return fullString;
  }
  return fullString.substring(0, maxLength - 3) + '...';
}

/**
 * Get formulation component count
 */
export function getComponentCount(formulation: FormulationData): number {
  if (formulation.num_components) {
    return formulation.num_components;
  }
  if (formulation.components) {
    return formulation.components.length;
  }
  if (formulation.HBD && formulation.HBA) {
    return 2;
  }
  return 0;
}
