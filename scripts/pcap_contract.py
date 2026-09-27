"""Check independently dissected X2AP fields against the pinned standard's IE contract."""
import json
import pathlib
import xml.etree.ElementTree as ET
CONTRACT=json.loads((pathlib.Path(__file__).resolve().parents[1]/'tests/x2_wire_contract.json').read_text())

def validate_x2_pdml(xml):
    root=ET.fromstring(xml)
    counts={}
    for packet in root.findall('packet'):
        proto=packet.find("proto[@name='x2ap']")
        if proto is None: continue
        pdu=proto.find(".//field[@name='x2ap.X2AP_PDU']")
        procedure=proto.find(".//field[@name='x2ap.procedureCode']")
        if pdu is None or procedure is None: raise ValueError('X2AP discriminator missing')
        key=pdu.attrib['show']+':'+procedure.attrib['show']
        message=CONTRACT['messages'].get(key)
        if message is None: raise ValueError('Unexpected X2 procedure '+key)
        ids=[]
        for field in proto.iter('field'):
            if field.attrib.get('name') not in ('x2ap.ProtocolIE_Field_element','x2ap.ProtocolIE_Single_Container_element'): continue
            identifier=field.find("field[@name='x2ap.id']")
            criticality=field.find("field[@name='x2ap.criticality']")
            if identifier is None or criticality is None: raise ValueError('Incomplete IE dissection')
            number=identifier.attrib['show']
            expected=message['criticality'].get(number)
            if expected is None or int(criticality.attrib['show'])!=expected:
                raise ValueError(message['name']+' IE '+number+' violates standard criticality')
            ids.append(int(number))
        if not set(message['mandatory']).issubset(ids): raise ValueError(message['name']+' lacks mandatory IEs')
        counts[message['name']]=counts.get(message['name'],0)+1
    return counts
