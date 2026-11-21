"""Inspection guidance for the selected PPE policy; not a work permit."""
ACTIONS = {
    'helmet': 'Obtain and wear a hard hat.',
    'vest': 'Obtain and wear a safety vest.',
    'mask': 'Obtain the task-required mask.',
    'glasses': 'Obtain and wear safety goggles.',
    'gloves': 'Wear required gloves on both hands.',
    'earmuffs': 'Obtain and wear hearing protection.',
}

def inspection_advice(items):
    missing = [key for key, state in items.items() if state == 'missing']
    if not items:
        return ['Not assessed: no PPE policy selected.', 'Enable required PPE and repeat inspection.', 'Contact: Safety Officer / Site Supervisor.']
    if any(state not in ('worn', 'missing') for state in items.values()):
        return ['Inspection incomplete. Recheck PPE.', 'Contact: Safety Officer / Site Supervisor.']
    if not missing:
        return ['All checked PPE detected.', 'Follow site rules and supervisor instructions.', 'This check is not a work permit.']
    result = ['HOLD: Do not start work.',
              'No required PPE detected.' if len(missing) == len(items) else 'Required PPE is incomplete.']
    result.extend(ACTIONS[key] for key in missing)
    result.extend(['Contact: Safety Officer / Site Supervisor.',
                   'Equipment: PPE Custodian / Storekeeper.',
                   'Correct missing PPE and repeat inspection.'])
    return result
