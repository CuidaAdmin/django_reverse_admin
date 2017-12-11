#pylint: skip-file
from django.contrib.admin import helpers, ModelAdmin
from django.contrib.admin.options import InlineModelAdmin
from django.contrib.admin.utils import flatten_fieldsets
from django.db import models
from django.db.models import OneToOneField, ForeignKey
from django.forms import ModelForm
from django.forms.formsets import all_valid
from django.forms.models import BaseModelFormSet, modelformset_factory
from django.utils.encoding import force_text
from django.utils.functional import curry
from django.utils.safestring import mark_safe
from django.utils.translation import ugettext as _
from django.core.exceptions import PermissionDenied

class ReverseInlineFormSet(BaseModelFormSet):
    '''
    A formset with either a single object or a single empty
    form. Since the formset is used to render a required OneToOne
    relation, the forms must not be empty.
    '''
    parent_fk_name = ''
    def __init__(self,
                 data=None,
                 files=None,
                 instance=None,
                 prefix=None,
                 queryset=None,
                 save_as_new=False):
        object = getattr(instance, self.parent_fk_name, None)
        if object:
            qs = self.model.objects.filter(pk=object.id)
        else:
            qs = self.model.objects.filter(pk=-1)
            self.extra = 1
        super(ReverseInlineFormSet, self).__init__(data, files,
                                                   prefix=prefix,
                                                   queryset=qs)
        for form in self.forms:
            form.empty_permitted = False

def reverse_inlineformset_factory(parent_model,
                                  model,
                                  parent_fk_name,
                                  form=ModelForm,
                                  fields=None,
                                  exclude=None,
                                  formfield_callback=lambda f: f.formfield()):

    if fields is None and exclude is None:
        related_fields = [f for f in model._meta.get_fields() if
                          (f.one_to_many or f.one_to_one) and
                          f.auto_created and not f.concrete]
        fields = [f.name for f in model._meta.get_fields() if f not in
                  related_fields]  # ignoring reverse relations
    kwargs = {
        'form': form,
        'formfield_callback': formfield_callback,
        'formset': ReverseInlineFormSet,
        'extra': 0,
        'can_delete': False,
        'can_order': False,
        'fields': fields,
        'exclude': exclude,
        'max_num': 1,
    }
    FormSet = modelformset_factory(model, **kwargs)
    FormSet.parent_fk_name = parent_fk_name
    return FormSet

class ReverseInlineModelAdmin(InlineModelAdmin):
    '''
    Use the name and the help_text of the owning models field to
    render the verbose_name and verbose_name_plural texts.
    '''
    def __init__(self,
                 parent_model,
                 parent_fk_name,
                 model, admin_site,
                 inline_type):
        self.template = 'admin/edit_inline/%s.html' % inline_type
        self.parent_fk_name = parent_fk_name
        self.model = model
        field_descriptor = getattr(parent_model, self.parent_fk_name)
        field = field_descriptor.field

        self.verbose_name_plural = field.verbose_name.title()
        self.verbose_name = field.help_text
        if not self.verbose_name:
            self.verbose_name = self.verbose_name_plural
        super(ReverseInlineModelAdmin, self).__init__(parent_model, admin_site)

    def get_formset(self, request, obj=None, **kwargs):
        if 'fields' in kwargs:
            fields = kwargs.pop('fields')
        elif self.get_fieldsets(request, obj):
            fields = flatten_fieldsets(self.get_fieldsets(request, obj))
        else:
            fields = None

        # want to combine exclude arguments - can't do that if they're None
        # also, exclude starts as a tuple - need to make it a list
        exclude = list(kwargs.get("exclude", []))
        exclude_2 = self.exclude or []
        # but need exclude to be None if result is an empty list
        exclude = exclude.extend(list(exclude_2)) or None

        defaults = {
            "form": self.form,
            "fields": fields,
            "exclude": exclude,
            "formfield_callback": curry(self.formfield_for_dbfield, request=request),
        }
        kwargs.update(defaults)
        return reverse_inlineformset_factory(self.parent_model,
                                             self.model,
                                             self.parent_fk_name,
                                             **kwargs)

class ReverseModelAdmin(ModelAdmin):
    '''
    Patched ModelAdmin class. The add_view method is overridden to
    allow the reverse inline formsets to be saved before the parent
    model.
    '''
    def __init__(self, model, admin_site):

        super(ReverseModelAdmin, self).__init__(model, admin_site)
        if self.exclude is None:
            self.exclude = []
        self.exclude = list(self.exclude)

        inline_instances = []
        for field_name in self.inline_reverse:

            kwargs = {}
            if isinstance(field_name, tuple):
                kwargs = field_name[1]
                field_name = field_name[0]

            field = model._meta.get_field(field_name)
            if isinstance(field, (OneToOneField, ForeignKey)):
                name = field.name
                parent = field.remote_field.model
                inline = ReverseInlineModelAdmin(model,
                                                 name,
                                                 parent,
                                                 admin_site,
                                                 self.inline_type)
                if kwargs:
                    inline.__dict__.update(kwargs)
                inline_instances.append(inline)
                self.exclude.append(name)
        self.tmp_inline_instances = inline_instances
        
    def get_inline_instances(self, request, obj=None):
        return self.tmp_inline_instances + super(ReverseModelAdmin, self).get_inline_instances(request, obj)

    def save_related(self, request, form, formsets, change):
        res = super(ReverseModelAdmin, self).save_related(request, form, formsets, change)
        for inline_instance in self.tmp_inline_instances:
            for formset in formsets:
                if formset.model == inline_instance.model:
                    if formset.forms[0].instance != getattr(form.instance, formset.parent_fk_name):
                        formset.forms[0].instance.save()
                        setattr(form.instance, formset.parent_fk_name, formset.forms[0].instance)
                        form.instance.save()
                    break
        return res
