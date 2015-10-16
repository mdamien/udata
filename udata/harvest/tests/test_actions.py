# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import logging

from datetime import datetime

from mock import patch

from udata.models import Dataset, PeriodicTask

from udata.tests import TestCase, DBTestMixin
from udata.tests.factories import OrganizationFactory, UserFactory

from .factories import (
    fake, HarvestSourceFactory, HarvestJobFactory,
    mock_initialize, mock_process, DEFAULT_COUNT as COUNT
)
from ..models import (
    HarvestSource, HarvestJob, HarvestError,
    VALIDATION_PENDING, VALIDATION_ACCEPTED, VALIDATION_REFUSED
)
from ..backends import BaseBackend
from .. import actions, signals


log = logging.getLogger(__name__)


class HarvestActionsTest(DBTestMixin, TestCase):
    def test_list_backends(self):
        for backend in actions.list_backends():
            self.assertTrue(issubclass(backend, BaseBackend))

    def test_list_sources(self):
        self.assertEqual(actions.list_sources(), [])

        sources = HarvestSourceFactory.create_batch(3)

        result = actions.list_sources()
        self.assertEqual(len(result), len(sources))

        for source in sources:
            self.assertIn(source, result)

    def test_list_sources_for_owner(self):
        owner = UserFactory()
        self.assertEqual(actions.list_sources(owner), [])

        sources = HarvestSourceFactory.create_batch(3, owner=owner)
        HarvestSourceFactory()

        result = actions.list_sources(owner)
        self.assertEqual(len(result), len(sources))

        for source in sources:
            self.assertIn(source, result)

    def test_list_sources_for_org(self):
        org = OrganizationFactory()
        self.assertEqual(actions.list_sources(org), [])

        sources = HarvestSourceFactory.create_batch(3, organization=org)
        HarvestSourceFactory()

        result = actions.list_sources(org)
        self.assertEqual(len(result), len(sources))

        for source in sources:
            self.assertIn(source, result)

    def test_create_source(self):
        source_url = fake.url()

        with self.assert_emit(signals.harvest_source_created):
            source = actions.create_source('Test source', source_url, 'factory')

        self.assertEqual(source.name, 'Test source')
        self.assertEqual(source.slug, 'test-source')
        self.assertEqual(source.url, source_url)
        self.assertEqual(source.backend, 'factory')
        self.assertEqual(source.frequency, 'manual')
        self.assertTrue(source.active)
        self.assertIsNone(source.owner)
        self.assertIsNone(source.organization)

        self.assertEqual(source.validation.state, VALIDATION_PENDING)
        self.assertIsNone(source.validation.on)
        self.assertIsNone(source.validation.by)
        self.assertIsNone(source.validation.comment)

    @patch('udata.harvest.actions.launch')
    def test_validate_source(self, mock):
        source = HarvestSourceFactory()

        actions.validate_source(source.id)

        source.reload()
        self.assertEqual(source.validation.state, VALIDATION_ACCEPTED)
        self.assertIsNotNone(source.validation.on)
        self.assertIsNone(source.validation.by)
        self.assertIsNone(source.validation.comment)
        mock.assert_called_once_with(source.id)

    @patch('udata.harvest.actions.launch')
    def test_validate_source_with_comment(self, mock):
        source = HarvestSourceFactory()

        actions.validate_source(source.id, 'comment')

        source.reload()

        self.assertEqual(source.validation.state, VALIDATION_ACCEPTED)
        self.assertIsNotNone(source.validation.on)
        self.assertIsNone(source.validation.by)
        self.assertEqual(source.validation.comment, 'comment')
        mock.assert_called_once_with(source.id)

    def test_reject_source(self):
        source = HarvestSourceFactory()

        actions.reject_source(source.id, 'comment')

        source.reload()
        self.assertEqual(source.validation.state, VALIDATION_REFUSED)
        self.assertIsNotNone(source.validation.on)
        self.assertIsNone(source.validation.by)
        self.assertEqual(source.validation.comment, 'comment')

    def test_get_source_by_slug(self):
        source = HarvestSourceFactory()
        self.assertEqual(actions.get_source(source.slug), source)

    def test_get_source_by_id(self):
        source = HarvestSourceFactory()
        self.assertEqual(actions.get_source(str(source.id)), source)

    def test_get_source_by_objectid(self):
        source = HarvestSourceFactory()
        self.assertEqual(actions.get_source(source.id), source)

    def test_delete_source_by_slug(self):
        source = HarvestSourceFactory()
        with self.assert_emit(signals.harvest_source_deleted):
            deleted_source = actions.delete_source(source.slug)

        self.assertIsNotNone(deleted_source.deleted)
        self.assertEqual(deleted_source.id, source.id)
        deleted_sources = HarvestSource.objects(deleted__exists=True)
        self.assertEqual(len(deleted_sources), 1)

    def test_delete_source_by_id(self):
        source = HarvestSourceFactory()
        with self.assert_emit(signals.harvest_source_deleted):
            deleted_source = actions.delete_source(str(source.id))

        self.assertIsNotNone(deleted_source.deleted)
        self.assertEqual(deleted_source.id, source.id)
        deleted_sources = HarvestSource.objects(deleted__exists=True)
        self.assertEqual(len(deleted_sources), 1)

    def test_delete_source_by_objectid(self):
        source = HarvestSourceFactory()
        with self.assert_emit(signals.harvest_source_deleted):
            deleted_source = actions.delete_source(source.id)

        self.assertIsNotNone(deleted_source.deleted)
        self.assertEqual(deleted_source.id, source.id)
        deleted_sources = HarvestSource.objects(deleted__exists=True)
        self.assertEqual(len(deleted_sources), 1)

    def test_get_job_by_id(self):
        job = HarvestJobFactory()
        self.assertEqual(actions.get_job(str(job.id)), job)

    def test_get_job_by_objectid(self):
        job = HarvestJobFactory()
        self.assertEqual(actions.get_job(job.id), job)

    def test_schedule(self):
        source = HarvestSourceFactory()
        with self.assert_emit(signals.harvest_source_scheduled):
            actions.schedule(str(source.id), hour=0)

        source.reload()
        self.assertEqual(len(PeriodicTask.objects), 1)
        periodic_task = PeriodicTask.objects.first()
        self.assertEqual(source.periodic_task, periodic_task)
        self.assertEqual(periodic_task.args, [str(source.id)])
        self.assertEqual(periodic_task.crontab.hour, '0')
        self.assertEqual(periodic_task.crontab.minute, '*')
        self.assertEqual(periodic_task.crontab.day_of_week, '*')
        self.assertEqual(periodic_task.crontab.day_of_month, '*')
        self.assertEqual(periodic_task.crontab.month_of_year, '*')
        self.assertTrue(periodic_task.enabled)
        self.assertEqual(periodic_task.name, 'Harvest {0}'.format(source.name))

    def test_unschedule(self):
        periodic_task = PeriodicTask.objects.create(
            task='harvest',
            name=fake.name(),
            description=fake.sentence(),
            enabled=True,
            crontab=PeriodicTask.Crontab()
        )
        source = HarvestSourceFactory(periodic_task=periodic_task)
        with self.assert_emit(signals.harvest_source_unscheduled):
            actions.unschedule(str(source.id))

        source.reload()
        self.assertEqual(len(PeriodicTask.objects), 0)
        self.assertIsNone(source.periodic_task)

    def test_purge_sources(self):
        to_delete = HarvestSourceFactory.create_batch(3, deleted=datetime.now())
        to_keep = HarvestSourceFactory.create_batch(2)

        result = actions.purge_sources()

        self.assertEqual(result, len(to_delete))
        self.assertEqual(len(HarvestSource.objects), len(to_keep))


class ExecutionTestMixin(DBTestMixin):
    def test_default(self):
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='factory', organization=org)
        with self.assert_emit(signals.before_harvest_job,
                              signals.after_harvest_job):
            self.action(source.slug)

        source.reload()
        self.assertEqual(len(HarvestJob.objects(source=source)), 1)

        job = source.get_last_job()

        self.assertEqual(job.status, 'done')
        self.assertEqual(job.errors, [])
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        self.assertEqual(len(job.items), COUNT)

        for item in job.items:
            self.assertEqual(item.status, 'done')
            self.assertEqual(item.errors, [])
            self.assertIsNotNone(item.started)
            self.assertIsNotNone(item.ended)
            self.assertIsNotNone(item.dataset)

            dataset = item.dataset
            self.assertIsNotNone(Dataset.objects(id=dataset.id).first())
            self.assertEqual(dataset.organization, org)
            self.assertIn('harvest:remote_id', dataset.extras)
            self.assertIn('harvest:last_update', dataset.extras)
            self.assertIn('harvest:source_id', dataset.extras)

        self.assertEqual(len(HarvestJob.objects), 1)
        self.assertEqual(len(Dataset.objects), COUNT)

    def test_error_on_initialize(self):
        def init(self):
            raise ValueError('test')

        source = HarvestSourceFactory(backend='factory')
        with self.assert_emit(signals.before_harvest_job),\
             mock_initialize.connected_to(init):
            self.action(source.slug)

        source.reload()
        self.assertEqual(len(HarvestJob.objects(source=source)), 1)

        job = source.get_last_job()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(len(job.errors), 1)
        error = job.errors[0]
        self.assertIsInstance(error, HarvestError)
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        self.assertEqual(len(job.items), 0)

        self.assertEqual(len(HarvestJob.objects), 1)
        self.assertEqual(len(Dataset.objects), 0)

    def test_error_on_item(self):
        def process(self, item):
            if item.remote_id == '1':
                raise ValueError('test')

        source = HarvestSourceFactory(backend='factory')
        with self.assert_emit(signals.before_harvest_job,
                              signals.after_harvest_job), \
                mock_process.connected_to(process):
            self.action(source.slug)

        source.reload()
        self.assertEqual(len(HarvestJob.objects(source=source)), 1)

        job = source.get_last_job()
        self.assertEqual(job.status, 'done-errors')
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        self.assertEqual(len(job.errors), 0)
        self.assertEqual(len(job.items), COUNT)

        items_ok = filter(lambda i: not len(i.errors), job.items)
        self.assertEqual(len(items_ok), COUNT - 1)

        for item in items_ok:
            self.assertIsNotNone(item.started)
            self.assertIsNotNone(item.ended)
            self.assertEqual(item.status, 'done')
            self.assertEqual(item.errors, [])

        item_ko = filter(lambda i: len(i.errors), job.items)[0]
        self.assertIsNotNone(item_ko.started)
        self.assertIsNotNone(item_ko.ended)
        self.assertEqual(item_ko.status, 'failed')
        self.assertEqual(len(item_ko.errors), 1)

        error = item_ko.errors[0]
        self.assertIsInstance(error, HarvestError)

        self.assertEqual(len(HarvestJob.objects), 1)
        self.assertEqual(len(Dataset.objects), COUNT - 1)


class HarvestLaunchTest(ExecutionTestMixin, TestCase):
    def action(self, *args, **kwargs):
        return actions.launch(*args, **kwargs)


class HarvestRunTest(ExecutionTestMixin, TestCase):
    def action(self, *args, **kwargs):
        return actions.run(*args, **kwargs)


class HarvestPreviewTest(DBTestMixin, TestCase):
    def test_preview(self):
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='factory', organization=org)

        job = actions.preview(source.slug)

        self.assertEqual(job.status, 'done')
        self.assertEqual(job.errors, [])
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        self.assertEqual(len(job.items), COUNT)

        for item in job.items:
            self.assertEqual(item.status, 'done')
            self.assertEqual(item.errors, [])
            self.assertIsNotNone(item.started)
            self.assertIsNotNone(item.ended)
            self.assertIsNotNone(item.dataset)

            dataset = item.dataset
            self.assertEqual(dataset.organization, org)
            self.assertIn('harvest:remote_id', dataset.extras)
            self.assertIn('harvest:last_update', dataset.extras)
            self.assertIn('harvest:source_id', dataset.extras)

        self.assertEqual(len(HarvestJob.objects), 0)
        self.assertEqual(len(Dataset.objects), 0)

    def test_preview_max_items(self):
        org = OrganizationFactory()
        source = HarvestSourceFactory(backend='factory',
                                      organization=org,
                                      config={'count': 10})

        self.app.config['HARVEST_PREVIEW_MAX_ITEMS'] = 5

        job = actions.preview(source.slug)

        self.assertEqual(len(job.items), 5)

    def test_preview_with_error_on_initialize(self):
        def init(self):
            raise ValueError('test')

        source = HarvestSourceFactory(backend='factory')

        with mock_initialize.connected_to(init):
            job = actions.preview(source.slug)

        self.assertEqual(job.status, 'failed')
        self.assertEqual(len(job.errors), 1)
        error = job.errors[0]
        self.assertIsInstance(error, HarvestError)
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        self.assertEqual(len(job.items), 0)

        self.assertEqual(len(HarvestJob.objects), 0)
        self.assertEqual(len(Dataset.objects), 0)

    def test_preview_with_error_on_item(self):
        def process(self, item):
            if item.remote_id == '1':
                raise ValueError('test')

        source = HarvestSourceFactory(backend='factory')

        with mock_process.connected_to(process):
            job = actions.preview(source.slug)

        self.assertEqual(job.status, 'done-errors')
        self.assertIsNotNone(job.started)
        self.assertIsNotNone(job.ended)
        self.assertEqual(len(job.errors), 0)
        self.assertEqual(len(job.items), COUNT)

        items_ok = filter(lambda i: not len(i.errors), job.items)
        self.assertEqual(len(items_ok), COUNT - 1)

        for item in items_ok:
            self.assertIsNotNone(item.started)
            self.assertIsNotNone(item.ended)
            self.assertEqual(item.status, 'done')
            self.assertEqual(item.errors, [])

        item_ko = filter(lambda i: len(i.errors), job.items)[0]
        self.assertIsNotNone(item_ko.started)
        self.assertIsNotNone(item_ko.ended)
        self.assertEqual(item_ko.status, 'failed')
        self.assertEqual(len(item_ko.errors), 1)

        error = item_ko.errors[0]
        self.assertIsInstance(error, HarvestError)

        self.assertEqual(len(HarvestJob.objects), 0)
        self.assertEqual(len(Dataset.objects), 0)