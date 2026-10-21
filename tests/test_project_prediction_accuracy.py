from test_project_resources import service, candidate, results


def test_scores_pre_execution_forecast_not_updated_history(tmp_path):
    engine=service(tmp_path); plan=candidate(tmp_path)
    with engine.reserve(plan, 'task') as reservation:
        before=reservation['estimated_cost']['ram_gib']
        observed=engine.observe(plan,results(plan),reservation=reservation)
    score=observed['prediction_accuracy']
    assert score['recorded']
    assert score['comparisons']['direct_ram_gib']['predicted']==before
    assert score['comparisons']['direct_ram_gib']['actual']==2
    assert score['comparisons']['direct_ram_gib']['underpredicted']
    assert 'direct_cpu_seconds' not in score['comparisons']
    assert service(tmp_path).review(plan)['prediction_accuracy']['direct_ram_gib']['samples']==1


def test_copy_mutation_replay_and_expired_reservation(tmp_path):
    engine=service(tmp_path); plan=candidate(tmp_path)
    with engine.reserve(plan,'task') as reservation:
        assert not engine.observe(plan,results(plan),reservation=dict(reservation))['prediction_accuracy']['recorded']
        original=reservation['estimated_cost']['ram_gib']
        reservation['estimated_cost']['ram_gib']=999
        first=engine.observe(plan,results(plan),reservation=reservation)['prediction_accuracy']
        assert first['comparisons']['direct_ram_gib']['predicted']==original
        assert not engine.observe(plan,results(plan),reservation=reservation)['prediction_accuracy']['recorded']
    assert not engine.observe(plan,results(plan),reservation=reservation)['prediction_accuracy']['recorded']


def test_failed_checks_and_changed_candidate_never_score(tmp_path):
    engine=service(tmp_path); plan=candidate(tmp_path)
    with engine.reserve(plan,'task') as reservation:
        data=results(plan);data['status']='failed'
        assert not engine.observe(plan,data,reservation=reservation)['prediction_accuracy']['recorded']
    with engine.reserve(plan,'task2') as reservation:
        assert not engine.observe({**plan,'content':'changed'},results(plan),reservation=reservation)['prediction_accuracy']['recorded']


def test_three_scopes_remain_separate(tmp_path):
    engine=service(tmp_path); plan=candidate(tmp_path);data=results(plan)
    for row in data['checks']:
        row['resource_measurements']['job_metrics']=dict(schema_version=1,scope='job_associated_processes',snapshot_complete=True,active_processes=0,total_processes=2,cpu_seconds=5)
    engine.observe(plan,data)
    with engine.reserve(plan,'task') as reservation:
        forecast=reservation['estimated_cost']['cpu_seconds']
        score=engine.observe(plan,data,reservation=reservation)['prediction_accuracy']['comparisons']
    assert score['direct_cpu_seconds']['actual']==4
    assert score['direct_cpu_seconds']['predicted']==forecast==6
    assert score['job_cpu_seconds']['actual']==10
    assert score['job_cpu_seconds']['predicted']==15


def test_optional_store_failure_does_not_change_observation_or_lease(tmp_path,monkeypatch):
    engine=service(tmp_path);plan=candidate(tmp_path)
    def fail():raise OSError('storage unavailable')
    monkeypatch.setattr(engine,'_accuracy_history',fail)
    with engine.reserve(plan,'task') as reservation:
        result=engine.observe(plan,results(plan),reservation=reservation)
        assert result['recorded'] and not result['prediction_accuracy']['recorded']
    assert not engine.scheduler.leases


def test_diagnostics_in_existing_backup_inventory(tmp_path):
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from prometheus_assistant.maintenance import create_backup,restore_backup
    from prometheus_assistant.prediction_accuracy import AccuracyHistory
    engine=service(tmp_path);plan=candidate(tmp_path)
    with engine.reserve(plan,'task') as reservation:
        assert engine.observe(plan,results(plan),reservation=reservation)['prediction_accuracy']['recorded']
    memory=tmp_path/'memory.sqlite3'
    with closing(sqlite3.connect(memory)) as db,db:db.execute('CREATE TABLE fixture(value TEXT)')
    backup=create_backup(memory,tmp_path/'backup',data_dirs={'Maintenance':tmp_path/'Maintenance'},require_separate_volume=False)
    restored=restore_backup(backup['path'],tmp_path/'restored')
    path=Path(restored['path'])/'Maintenance/ResourceHistory/prediction-accuracy.sqlite3'
    assert path.is_file()
    assert AccuracyHistory(path).summary(engine._category(plan))['direct_ram_gib']['samples']==1
