"""Entirely synthetic wire packets: no hardware logs or surveyed coordinates."""

def make_packet(ready=False):
    device='synthetic-device';flight='synthetic-flight'
    epoch=1700000000000000000
    site=dict(site_id='synthetic-site',name='Synthetic origin only',origin_lon_deg=0,
              origin_lat_deg=0,origin_alt_m=0,vertical_datum='WGS84_ellipsoidal',frame='ENU')
    observations=[]
    for seq in range(1,6):
        ident=device+':'+str(seq);stamp=str(epoch+seq*1000000000)
        pose=dict(pose_id='synthetic-pose-'+str(seq),flight_id=flight,measured_at_ns=stamp,
                  x_m=0,y_m=0,z_m=0,lon_deg=0,lat_deg=0,ellipsoid_alt_m=0,quality_json={'synthetic':True})
        obs=dict(observation_id=ident,pose_id=pose['pose_id'] if ready else None,device_id=device,
                 sequence=seq,query_started_at_ns=stamp,query_finished_at_ns=stamp,received_at_ns=stamp,
                 pose_time_offset_ns='0' if ready else None,raw_response='Synthetic response; no modem log',quality_json={'synthetic':True})
        cells=[dict(observation_id=ident,role='serving' if i==0 else 'neighbor',rat='LTE',
                    mcc='001',mnc='01',cell_id='000001' if i==0 else None,tac='0001' if i==0 else None,
                    pci=i+1,arfcn=100,rsrp_dbm=-80-i,rsrq_db=-8,rssi_dbm=-60,sinr_db=10,
                    extras_json={'synthetic':True}) for i in range(5 if ready else 2)]
        observations.append(dict(ready_for_database=ready,missing_requirements=[] if ready else ['no_valid_gnss','site_origin_not_configured'],
                                 pose=pose if ready else None,radio_observation=obs,cell_measurements=cells))
    return dict(format='uav-radio-mapping.radio.v1',database_schema_version=1,packet_id='synthetic-packet',
                created_at_ns=str(epoch),site=site if ready else None,provenance={'synthetic':True},
                flight=dict(flight_id=flight,site_id=site['site_id'] if ready else None,device_id=device,
                            started_at_ns=str(epoch),ended_at_ns=None,metadata_json={'synthetic':True}),observations=observations)
