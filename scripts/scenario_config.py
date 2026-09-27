"""Validate the currently supported lab profile without scientific dependencies."""
import math

def finite(value, field, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(field+' must be a finite number')
    if minimum is not None and value < minimum:
        raise ValueError(field+' is below its minimum')

def position(value, field):
    if not isinstance(value, list) or len(value)!=2:
        raise ValueError(field+' needs [x,y] coordinates')
    for coordinate in value: finite(coordinate, field)

def validate(scenario):
    if scenario['sample_rate_hz'] != 1920000 or scenario['frame_samples'] != 1920:
        raise ValueError('Current RF profile requires 1.92 MHz and 1920-sample frames')
    cells=scenario['cells']
    if len(cells)!=2 or {c['name'] for c in cells}!={'enb1','enb2'}:
        raise ValueError('Current profile requires exactly enb1 and enb2')
    if len({c['enb_id'] for c in cells})!=2 or len({c['pci'] for c in cells})!=2:
        raise ValueError('eNB IDs and PCIs must be distinct')
    for cell in cells:
        position(cell['position_m'],cell['name']+'.position_m')
        finite(cell['tx_power_dbm'],cell['name']+'.tx_power_dbm')
        finite(cell.get('beamwidth_deg',360),'beamwidth_deg',1e-9)
        finite(cell.get('max_antenna_loss_db',0),'max_antenna_loss_db',0)
        finite(cell.get('rx_gain_db',0),'rx_gain_db')
        finite(cell.get('azimuth_deg',0),'azimuth_deg')
        for field,maximum in [('pci',503),('enb_id',0xfffff)]:
            if isinstance(cell[field],bool) or not isinstance(cell[field],int) or not 0<=cell[field]<=maximum:
                raise ValueError('Invalid '+field)
        settings=cell.get('channel',{})
        if set(settings)-{'delay_samples','doppler_hz','fading','coherence_frames'}:
            raise ValueError('Unknown per-cell channel setting')
        delay=settings.get('delay_samples',0)
        if isinstance(delay,bool) or not isinstance(delay,int) or not 0<=delay<=1920:
            raise ValueError('delay_samples must be an integer within one frame')
        finite(settings.get('doppler_hz',0),'doppler_hz')
        if settings.get('fading','none') not in ('none','block_rayleigh'):
            raise ValueError('Unsupported fading model')
        coherence=settings.get('coherence_frames',100)
        if isinstance(coherence,bool) or not isinstance(coherence,int) or coherence<1:
            raise ValueError('coherence_frames must be a positive integer')
    position(scenario['ue']['position_m'],'ue.position_m')
    finite(scenario['ue'].get('tx_power_dbm',23),'ue.tx_power_dbm')
    model=scenario['channel']
    for field in ('reference_distance_m','path_loss_exponent'):
        finite(model[field],field,1e-9)
    for field in ('reference_loss_db','iq_reference_dbm'):
        finite(model[field],field)
    finite(model.get('ul_iq_reference_dbm',-47),'ul_iq_reference_dbm')
    finite(model['noise_std_iq'],'noise_std_iq',0)
    if not isinstance(model['seed'],int) or isinstance(model['seed'],bool) or model['seed']<0:
        raise ValueError('seed must be a nonnegative integer')
    return scenario
