import type { BcpBand, RequestType, SupplierRequest } from '../api/types';

export function bcpBandLabel(band: BcpBand): string {
  switch (band) {
    case 'LOW_RISK': return 'Low risk';
    case 'MEDIUM_RISK': return 'Medium risk';
    case 'HIGH_RISK': return 'High risk';
  }
}

export function requestTypeLabel(requestType: RequestType): string {
  switch (requestType) {
    case 'IMPACT': return 'Impact';
    case 'BACKUP_ACTIVATION': return 'Backup activation';
    case 'PULL_IN': return 'Pull-in';
  }
}

export function requestStatusLabel(request: SupplierRequest): string {
  if (request.status === 'NO_RESPONSE') return 'No response';
  if (request.status === 'REQUESTED') return 'Requested';
  return request.answer ? `Answered: ${request.answer}` : 'Answered';
}
