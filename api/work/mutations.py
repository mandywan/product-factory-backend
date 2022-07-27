import trace
from django.db import IntegrityError
from graphene_file_upload.scalars import Upload
from entitlements.exceptions import ValidationError

import notification.tasks
from commercial.models import ProductOwner
from contribution_management.models import ContributorAgreement, ContributorAgreementAcceptance
from matching.models import BountyDeliveryAttempt, BountyClaim, BountyDeliveryAttachment, CLAIM_TYPE_ACTIVE, \
    CLAIM_TYPE_FAILED, CLAIM_TYPE_DONE
from notification.models import Notification
from .types import *
from work.models import *
from talent.models import ProductPerson, Person
from git.views import create_webhook
from api.utils import is_admin_or_manager, is_admin
from api.mutations import InfoStatusMutation
from .utils import set_depends
from api.decorators import is_current_person
from ..images.utils import upload_photo, upload_file
from ..types import InfoType
from datetime import datetime
import json

class CreateProductMutation(graphene.Mutation, InfoType):
    class Arguments:
        file = Upload(required=False)
        product_input = ProductInput(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, product_input, file=None):
        url = None

        if file:
            url = upload_photo(file, 'products')

        products = Product.objects.filter(name=product_input.name).count()

        if products > 0:
            return CreateProductMutation(status=False, message='Product name already exits')

        product_owner = ProductOwner.get_or_create(current_person)

        new_product = Product.objects.create(
            photo=url,
            name=product_input.name,
            short_description=product_input.short_description,
            full_description=product_input.full_description,
            website=product_input.website,
            video_url=product_input.get('video_url', None),
            is_private=product_input.get('is_private', False),
            owner=product_owner
        )

        current_headline = current_person.headline
        current_person.headline = f'{current_headline}, Admin - {product_input.name}' if len(
            current_headline) > 0 else f'Admin - {product_input.name}'
        current_person.save()

        new_product_person = ProductPerson(
            person=current_person,
            product=new_product,
            right=ProductPerson.PERSON_TYPE_PRODUCT_ADMIN
        )
        new_product_person.save()

        return CreateProductMutation(status=True, message='Product successfully created')
        # except:
        #     return CreateProductMutation(status=False, message='Error with product creation')


class UpdateProductMutation(graphene.Mutation, InfoType):
    class Arguments:
        file = Upload(required=False)
        product_input = ProductInput(required=True)

    new_slug = graphene.String()

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, product_input, file=None):
        try:
            if is_admin(current_person.id, product_input.slug):
                product = Product.objects.get(slug=product_input.slug)

                product.photo = upload_photo(file, 'products')
                product.name = product_input.name
                product.short_description = product_input.short_description
                product.full_description = product_input.full_description
                product.website = product_input.website
                product.video_url = product_input.get('video_url', None)
                product.is_private = product_input.get('is_private', False)
                product.save()

                return UpdateProductMutation(new_slug=product.slug, status=True, message='Product successfully updated')
            else:
                return UpdateProductMutation(
                    new_slug=None, status=False, message="You don't have permission for update that product"
                )
        except:
            return UpdateProductMutation(new_slug=None, status=False, message='Error with product updating')


class DeleteProductMutation(graphene.Mutation, InfoType):
    class Arguments:
        slug = graphene.String(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, slug):
        try:
            product = Product.objects.get(slug=slug)

            if is_admin(current_person.id, slug):
                product.delete()

                return DeleteProductMutation(status=True, message='Product successfully deleted')
            else:
                return DeleteProductMutation(status=False, message="You don't have permission for delete that product")
        except:
            return DeleteProductMutation(status=False, message='Error with product deletion')


class CreateCapabilityMutation(graphene.Mutation):
    class Arguments:
        input = CapabilityInput(required=True)

    status = graphene.Boolean()
    capability = graphene.Field(CapabilityType)

    @staticmethod
    def mutate(*args, **kwargs):
        try:
            arguments = kwargs.get('input')

            if arguments.node_id is None:
                node_id = Product.objects.get(slug=arguments.product_slug).capability_start_id

                if not node_id:
                    updated_product = Product.objects.get(slug=arguments.product_slug)
                    new_root = Capability.add_root(name=updated_product.name)
                    updated_product.capability_start_id = new_root.id
                    updated_product.save()

                    node_id = updated_product.capability_start_id

                parent_node = Capability.objects.get(pk=node_id)
            else:
                parent_node = Capability.objects.get(pk=arguments.node_id)

            new_node = parent_node.add_child(
                name=arguments.name,
                description=arguments.description,
                video_link=(arguments.video_link if arguments.video_link is not None else '')
            )

            # if arguments.stacks is not None:
            #     for stack_id in arguments.stacks:
            #         new_capability_stack = CapabilityStack(
            #             capability=Capability(new_node).id, stack=Stack(stack_id)
            #         )
            #         new_capability_stack.save()

            if arguments.attachments is not None:
                for attachment_id in arguments.attachments:
                    new_capability_attachment = CapabilityAttachment(
                        capability=Capability(new_node).id, attachment=Attachment(attachment_id)
                    )
                    new_capability_attachment.save()

            return CreateCapabilityMutation(status=True, capability=new_node)
        except Exception as e:
            print(e)
            return CreateCapabilityMutation(status=False, capability=None)


class DeleteCapabilityMutation(graphene.Mutation):
    class Arguments:
        node_id = graphene.Int(required=True)

    status = graphene.Boolean()
    capability_id = graphene.Int()

    @staticmethod
    def mutate(self, info, **kwargs):
        node_id = kwargs.get('node_id')

        try:
            capability = Capability.objects.get(pk=node_id)
            capability.delete()

            return DeleteCapabilityMutation(status=True, capability_id=node_id)
        except Exception as e:
            print(e)
            return DeleteCapabilityMutation(status=False)


class UpdateCapabilityMutation(graphene.Mutation):
    class Arguments:
        input = CapabilityInput(required=True)

    status = graphene.Boolean()
    capability = graphene.Field(CapabilityType)

    @staticmethod
    def mutate(self, info, **kwargs):
        arguments = kwargs.get('input')

        try:
            capability = Capability.objects.get(pk=arguments.node_id)

            if arguments.product_slug is not None:
                product = Product.objects.get(slug=arguments.product_slug)
                capability.product_id = product.id

            if arguments.name is not None:
                capability.name = arguments.name

            if arguments.description is not None:
                capability.description = arguments.description

            # if arguments.stacks is not None:
            #     stacks = CapabilityStack.objects.filter(capability_id=arguments.node_id)
            #     stacks.delete()
            #
            #     for stack_id in arguments.stacks:
            #         new_capability_stack = CapabilityStack(
            #             capability_id=arguments.node_id, stack=Stack(stack_id)
            #         )
            #         new_capability_stack.save()

            if arguments.video_link is not None:
                capability.video_link = arguments.video_link

            capability.save()

            return UpdateCapabilityMutation(status=True, capability=capability)
        except Exception as e:
            print(e)
            return UpdateCapabilityMutation(status=False, capability=None)


class UpdateCapabilityTreeMutation(graphene.Mutation):
    class Arguments:
        product_slug = graphene.String(required=True)
        tree = graphene.JSONString(required=True)

    status = graphene.Boolean()

    @staticmethod
    def transform_tree_item(tree_item):
        tree_item = list(map(lambda tree_node: {
            'id': tree_node['id'],
            'data': {
                'name': tree_node['title'],
                'description': tree_node['description'],
                'video_link': tree_node['videoLink']
            },
            'children': UpdateCapabilityTreeMutation.transform_tree_item(tree_node['children'])
        }, tree_item))

        return tree_item

    @staticmethod
    def mutate(*args, **kwargs):
        product_slug = kwargs.get('product_slug')
        tree = kwargs.get('tree')

        tree = UpdateCapabilityTreeMutation.transform_tree_item(tree)

        node_id = Product.objects.get(slug=product_slug).capability_start_id

        Capability.load_bulk(tree, parent=Capability.objects.get(pk=node_id), keep_ids=True)

        return UpdateCapabilityTreeMutation(status=True)


class CreateInitiativeMutation(graphene.Mutation):
    class Arguments:
        input = InitiativeInput(
            required=True,
            description="Fields required to create a initiative",
        )

    initiative = graphene.Field(InitiativeType)

    def mutate(cls, instance, input):
        try:
            product = Product.objects.get(slug=input.product_slug)
        except:
            product = None

        initiative = Initiative(name=input.name,
                                product=product,
                                description=input.description,
                                video_url=input.get("video_url", None))
        if input.status is not None:
            initiative.status = input.status

        initiative.save()

        return CreateInitiativeMutation(initiative=initiative)


class UpdateInitiativeMutation(graphene.Mutation):
    class Arguments:
        id = graphene.Int(required=True)
        input = InitiativeInput(required=True)

    status = graphene.Boolean()
    initiative = graphene.Field(InitiativeType)

    @staticmethod
    def mutate(root, info, id, input=None):
        status = False
        try:
            product = Product.objects.get(slug=input.product_slug)

            initiative = Initiative.objects.get(pk=id)
            initiative.name = input.name
            initiative.product = product
            initiative.video_url = input.get("video_url", None)
            if input.description is not None:
                initiative.description = input.description
            if input.status is not None:
                initiative.status = input.status
            initiative.save()
            status = True
            return UpdateInitiativeMutation(status=status, initiative=initiative)
        except:
            pass
        return UpdateInitiativeMutation(status=status, initiative=None)


class DeleteInitiativeMutation(graphene.Mutation):
    class Arguments:
        id = graphene.Int(required=True)

    status = graphene.Boolean()
    initiative_id = graphene.Int()

    @staticmethod
    def mutate(root, info, id, input=None):
        status = False
        try:
            initiative = Initiative.objects.get(pk=id)
            initiative.delete()
            status = True
            return DeleteInitiativeMutation(status=status, initiative_id=id)
        except:
            pass
        return DeleteInitiativeMutation(status=status)


class CreateChallengeMutation(graphene.Mutation, InfoType):
    class Arguments:
        input = TaskInput(required=True)

    challenge = graphene.Field(TaskType)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        task_input = kwargs.get("input")
        product_slug = task_input.get("product_slug", None)
        priority = task_input.get("priority", None)

        try:
            product = Product.objects.get(slug=product_slug)
        except Product.DoesNotExist:
            return CreateChallengeMutation(task=None, status=False, message="Product doesn't exist")

        if not is_admin_or_manager(current_person, product_slug):
            return CreateChallengeMutation(task=None, status=False, message="You don't have permissions")

        try:
            initiative = Initiative.objects.get(pk=task_input.initiative)
        except:
            initiative = None

        try:
            capability = Capability.objects.get(pk=task_input.capability)
        except:
            capability = None

        status = int(task_input.status) if task_input.status else 0

        try:
            skill = None

            challenge = Challenge(
                initiative=initiative,
                capability=capability,
                title=task_input.title,
                description=task_input.description,
                status=status,
                created_by=current_person,
                updated_by=current_person,
                reviewer=Person.objects.get(user__username=task_input.reviewer),
                product=product,
                video_url=task_input.get("video_url", None),
                contribution_guide_id=task_input.get("contribution_guide", None),
                skill=skill
            )
        except Person.DoesNotExist:
            raise Exception("Reviewer is a required field")

        if task_input.short_description is not None:
            challenge.short_description = task_input.short_description

        if priority:
            challenge.priority = priority
        challenge.save()

        expertises = task_input.get("expertise", "[]")
        expertises = json.loads(expertises)
        for exp in expertises:
            expertise = Expertise.objects.get(id=exp)
            challenge.expertise.add(expertise)

        set_depends(task_input.depend_on, challenge.id)
        if task_input.get('tag', None):
            for tag in task_input.tags:
                if Tag.objects.filter(name=tag).count() <= 0:
                    Tag.objects.create(name=tag)
                challenge.tag.add(Tag.objects.get(name=tag))

        if product_slug:
            challenge.productchallenge_set.create(product=Product.objects.get(slug=product_slug))

        # create necessary bounty for this challenge
        bounty_skills = task_input.bounty_skills
        bounty_skills = json.loads(bounty_skills)
        
        for bounty in bounty_skills:
            bounty_skill = Skill.objects.get(id=bounty['skill']['id'])
            try:
                challenge_bounty = Bounty.objects.get(challenge=challenge, skill=bounty_skill)
                challenge_bounty.points = bounty['points']
            except Bounty.DoesNotExist:
                challenge_bounty = Bounty(challenge=challenge, skill=bounty_skill, points=bounty['points'])
            challenge_bounty.save()

            challenge_bounty.expertise.clear()
            for expertise in bounty['expertise']:
                challenge_bounty.expertise.add(Expertise.objects.get(id=expertise['id']))


        return CreateChallengeMutation(task=challenge, status=True, message="Task has been created successfully")


class UpdateChallengeMutation(graphene.Mutation, InfoType):
    class Arguments:
        id = graphene.Int(required=True)
        input = TaskInput(required=True)

    task = graphene.Field(TaskType)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        try:
            id = kwargs.get("id")
            task_input = kwargs.get("input")
            product_slug = task_input.get("product_slug", None)
            priority = task_input.get("priority", None)

            if not is_admin_or_manager(current_person, product_slug):
                return UpdateChallengeMutation(task=None, status=False, message="You don't have permissions")

            try:
                initiative = Initiative.objects.get(pk=task_input.initiative)
            except:
                initiative = None

            try:
                capability = Capability.objects.get(pk=task_input.capability)
            except:
                capability = None

            skill = None
            challenge = Challenge.objects.get(pk=id)

            if task_input.status is not None:
                if int(task_input.status) == challenge.CHALLENGE_STATUS_CLAIMED and challenge.taskclaim_set.filter(
                        kind__in=[0, 1]
                ).count() <= 0:
                    return UpdateChallengeMutation(
                        task=None, status=False,
                        message="You cannot change status to claimed because the challenge is not assigned"
                    )

                has_claimed_bounty = False
                for bounty in challenge.bounty_set.all():
                    if bounty.bountyclaim_set.filter(kind__in=[0, 1]).count() > 0:
                        has_claimed_bounty = True

                if int(task_input.status) in [
                    Challenge.CHALLENGE_STATUS_DRAFT, Challenge.CHALLENGE_STATUS_BLOCKED
                ] and has_claimed_bounty:
                    return UpdateChallengeMutation(
                        task=None, status=False,
                        message="You cannot change status to that because challenge is claimed"
                    )
                else:
                    # when making status to available, change all active task claims to failed status
                    if int(task_input.status) == Challenge.CHALLENGE_STATUS_AVAILABLE and has_claimed_bounty:
                        for bounty in challenge.bounty_set.all():
                            claimed_bounty = bounty.bountyclaim_set.filter(kind__in=[0,1])
                            if claimed_bounty.count() > 0:
                                for cb in claimed_bounty:
                                    cb.kind = 2
                                    cb.save()

                    status = int(task_input.status)
            else:
                status = Challenge.CHALLENGE_STATUS_DRAFT

            challenge.initiative = initiative
            challenge.capability = capability
            challenge.skill = skill
            challenge.title = task_input.title
            challenge.description = task_input.description
            challenge.status = status
            challenge.video_url = task_input.get("video_url", None)
            challenge.contribution_guide_id = task_input.get("contribution_guide", None)

            if priority:
                challenge.priority = priority

            try:
                challenge.reviewer = Person.objects.get(user__username=task_input.reviewer)
            except:
                raise Exception("Reviewer is a required field")

            challenge.updated_by = current_person

            set_depends(depends=task_input.depend_on, challenge_id=id)

            if task_input.tags is not None:
                challenge.tag.clear()

                for tag in task_input.tags:
                    if Tag.objects.filter(name=tag).count() <= 0:
                        Tag.objects.create(name=tag)

                    challenge.tag.add(Tag.objects.get(name=tag))

            if task_input.short_description is not None:
                challenge.short_description = task_input.short_description
            challenge.save()

            expertises = task_input.get("expertise", "[]")
            expertises = json.loads(expertises)

            # remove any old expertise
            challenge.expertise.clear()
            
            for exp in expertises:
                expertise = Expertise.objects.get(id=exp)
                challenge.expertise.add(expertise)

            # create/update necessary bounty for this challenge
            bounty_skills = task_input.bounty_skills
            bounty_skills = json.loads(bounty_skills)
            
            for bounty in bounty_skills:
                bounty_skill = Skill.objects.get(id=bounty['skill']['id'])
                try:
                    challenge_bounty = Bounty.objects.get(challenge=challenge, skill=bounty_skill)
                    challenge_bounty.points = bounty['points']
                except Bounty.DoesNotExist:
                    challenge_bounty = Bounty(challenge=challenge, skill=bounty_skill, points=bounty['points'])
                challenge_bounty.save()

                challenge_bounty.expertise.clear()
                for expertise in bounty['expertise']:
                    challenge_bounty.expertise.add(Expertise.objects.get(id=expertise['id']))

            return UpdateChallengeMutation(task=None, status=True, message="Challenge has been updated successfully")
        except Exception as ex:
            print(ex)
            import traceback
            traceback.print_exc()
            return UpdateChallengeMutation(task=None, status=False, message="Error with challenge updating")


class DeleteChallengeMutation(graphene.Mutation):
    class Arguments:
        id = graphene.Int(required=True)

    status = graphene.Boolean()
    task_id = graphene.Int()

    @staticmethod
    def mutate(root, info, id, input=None):
        status = False
        try:
            challenge = Challenge.objects.get(pk=id)
            challenge.delete()
            status = True
            return DeleteChallengeMutation(status=status, task_id=id)
        except:
            pass
        return DeleteChallengeMutation(status=status)


class CreateCodeRepositoryMutation(graphene.Mutation):
    class Arguments:
        input = CodeRepositoryInput(
            required=True,
            description="Fields required to create a task",
        )

    repository = graphene.Field(CodeRepositoryType)
    error = graphene.String()

    def mutate(cls, instance, input):
        try:
            product = Product.objects.get(slug=input.product_slug)
        except:
            raise Exception("product slug is required!")

        repos = CodeRepository.objects.filter(product=product,
                                              repository=input.repository)
        if len(repos) > 0:
            raise Exception("repository already eixts!")

        git_owner = input.repository.split("/")[3]
        repo_name = input.repository.split("/")[4]
        res = create_webhook(git_owner, repo_name, input.access_token)
        try:
            error = res["error"]
            return CreateCodeRepositoryMutation(repository=None, error=error["message"])
        except:
            pass
        try:
            error = res["error"]
            return CreateCodeRepositoryMutation(repository=None, error=error["message"])
        except:
            pass
        try:
            repository = CodeRepository(product=product,
                                        repository=input.repository,
                                        git_access_token=input.access_token,
                                        git_owner=git_owner)
            repository.save()
        except:
            raise Exception("Repository creation failed!")

        return CreateCodeRepositoryMutation(repository=repository)


class CreateAttachmentMutation(graphene.Mutation):
    class Arguments:
        input = AttachmentInput(
            required=True
        )

    attachment = graphene.Field(AttachmentType)
    error = graphene.String()

    @staticmethod
    def mutate(*args, **kwargs):
        attachment_input = kwargs.get('input')

        try:
            attachment_input.file_type = attachment_input.file_type if (
                    attachment_input.file_type == 'file' or attachment_input.file_type == 'link'
                    or attachment_input.file_type == 'video'
            ) else 'link'

            attachment = Attachment(
                name=attachment_input.name,
                path=attachment_input.path,
                file_type=attachment_input.file_type
            )
            attachment.save()

            return CreateAttachmentMutation(attachment=attachment)
        except Exception as e:
            return CreateAttachmentMutation(attachment=None, error=str(e))


class DeleteAttachmentMutation(graphene.Mutation):
    class Arguments:
        id = graphene.Int(required=True)
        capability_id = graphene.Int(required=True)

    status = graphene.Boolean()
    attachment_id = graphene.Int()

    @staticmethod
    def mutate(root, info, id, capability_id, input=None):
        try:
            capability = Capability.objects.get(id=capability_id)
        except:
            raise Exception("Capability id is invalid!")

        try:
            attachment = Attachment.objects.get(pk=id)
            capability.attachment.remove(attachment)
            attachment.delete()
            return DeleteAttachmentMutation(status=True,
                                            attachment_id=id)
        except:
            pass
        return DeleteAttachmentMutation(status=False)


class ChangeTaskPriorityMutation(graphene.Mutation):
    class Arguments:
        task_id = graphene.Int(required=True)
        priority = graphene.String(required=True)

    status = graphene.Boolean()

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        try:
            priority = kwargs.get('priority')
            task_id = kwargs.get('task_id')

            if priority == 'High':
                priority = 0
            elif priority == 'Medium':
                priority = 1
            elif priority == 'Low':
                priority = 2
            else:
                raise Exception

            updated_task = Task.objects.get(pk=task_id)
            updated_task.priority = priority
            updated_task.save()
            return ChangeTaskPriorityMutation(status=True)
        except:
            return ChangeTaskPriorityMutation(status=False)


class LeaveTaskMutation(InfoStatusMutation, graphene.Mutation):
    class Arguments:
        task_id = graphene.Int(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        try:
            task_id = kwargs.get("task_id")
            task = Task.objects.get(id=task_id)
            task_claim = task.taskclaim_set.filter(person=current_person,
                                                   kind__in=[CLAIM_TYPE_DONE, CLAIM_TYPE_ACTIVE]).all()
            if len(task_claim) == 1:
                t_c = task_claim[0]
                t_c.kind = CLAIM_TYPE_FAILED
                t_c.save()
            elif len(task_claim) > 1:
                task_claim.update(kind=CLAIM_TYPE_FAILED)
            else:
                return LeaveTaskMutation(success=False, message="The task claim was not found")

            task.status = Task.TASK_STATUS_AVAILABLE
            task.updated_at = datetime.now()
            task.save()

            return LeaveTaskMutation(success=True, message="The task was successfully unassigned")
        except Task.DoesNotExist:
            return LeaveTaskMutation(success=False, message="The task doesn't exist")
        except TaskClaim.DoesNotExist:
            return LeaveTaskMutation(success=False, message="The task claim doesn't exist")


class InReviewTaskMutation(InfoStatusMutation, graphene.Mutation):
    class Arguments:
        task_id = graphene.Int(required=True)
        file_list = graphene.List(Upload)
        delivery_message = graphene.String()

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, task_id, delivery_message, file_list):
        try:
            task = Task.objects.get(id=task_id)

            task_claim = task.taskclaim_set.filter(person=current_person, kind=CLAIM_TYPE_ACTIVE, task=task).last()
            if not task_claim:
                return InReviewTaskMutation(success=False, message="The task claim was not found")

            task_delivery_attempt = TaskDeliveryAttempt.objects.create(kind=0, person=current_person,
                                                                       task_claim=task_claim,
                                                                       delivery_message=delivery_message)
            attachments = []
            for i in file_list:
                attachments.append(TaskDeliveryAttachment.objects.create(**upload_file(i, 'review'),
                                                                         task_delivery_attempt=task_delivery_attempt))

            task_claim.kind = CLAIM_TYPE_IN_REVIEW
            task_claim.save()
            if task.reviewer:
                notification.tasks.send_notification.delay([Notification.Type.EMAIL],
                                                           Notification.EventType.TASK_IN_REVIEW,
                                                           receivers=[task.reviewer.id],
                                                           title=task.title,
                                                           link=task.get_challenge_link())

            # call task save event to update tasklisting model
            task.status = Task.TASK_STATUS_IN_REVIEW
            task.save()
            task_from_list = TaskListing.objects.get(task_id=task.id)
            task_from_list.status = Task.TASK_STATUS_IN_REVIEW
            task.updated_at = datetime.now()
            task.save()

            return InReviewTaskMutation(success=True, message='The task status was changed to "In review"')
        except Task.DoesNotExist:
            return InReviewTaskMutation(success=False, message="The task doesn't exist")


class ClaimTaskMutation(InfoStatusMutation, graphene.Mutation):
    class Arguments:
        bounty_id = graphene.Int(required=True)

    success = graphene.Boolean()
    is_need_agreement = graphene.Boolean()
    message = graphene.String()
    claimed_task_link = graphene.String()
    claimed_task_name = graphene.String()

    @staticmethod
    def get_is_need_agreement(user_id, challenge_id):
        product = ProductChallenge.objects.get(challenge_id=challenge_id).product
        agreements = ContributorAgreement.objects.filter(product=product)

        if agreements.count() > 0:
            current_agreement_content = agreements.last().agreement_content

            if current_agreement_content not in ['', '<p><br></p>'] and \
                    not ContributorAgreementAcceptance.objects.filter(
                        person_id=user_id,
                        agreement=agreements.last()
                    ).exists():
                return True

        return False

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, bounty_id):
        try:
            bounty = Bounty.objects.get(id=bounty_id)
            challenge = bounty.challenge
            challenge_id = challenge.id

            is_need_agreement = ClaimTaskMutation.get_is_need_agreement(current_person.id, challenge_id)
            if is_need_agreement:
                return ClaimTaskMutation(
                    success=True,
                    message="Please, agree Contribution License Agreement first",
                    is_need_agreement=is_need_agreement
                )

            success = True
            message = "The bounty was successfully claimed"
            if not current_person:
                return ClaimTaskMutation(success=False,
                                        message="You cannot claim the bounty, please authenticate to the system")

            if get_right_task_status(challenge_id) == Challenge.CHALLENGE_STATUS_BLOCKED:
                return ClaimTaskMutation(
                    success=False,
                    is_need_agreement=False,
                    message="You cannot claim the bounty, when it is blocked"
                )

            claimed_bounty = current_person.bountyclaim_set.filter(kind=CLAIM_TYPE_ACTIVE).last()
            if claimed_bounty:
                claimed_challenge = claimed_bounty.bounty.challenge
                return ClaimTaskMutation(
                    success=False,
                    is_need_agreement=False,
                    message="""
                        You cannot claim the bounty because you have an active bounty.
                        Please complete current bounty first to claim a new bounty.
                    """,
                    claimed_task_link=claimed_challenge.get_challenge_link(False),
                    claimed_task_name=claimed_challenge.title
                )

            # create a new bounty claim with "Active" status if task has "auto_approve_task_claims" value
            if challenge.auto_approve_task_claims:
                try:
                    with transaction.atomic():
                        bounty_claim_inst = BountyClaim(
                            kind=CLAIM_TYPE_ACTIVE,
                            bounty=bounty,
                            person_id=current_person.id
                        )
                        bounty_claim_inst.save()

                        challenge.status = Challenge.CHALLENGE_STATUS_CLAIMED
                        challenge.updated_at = datetime.now()
                        challenge.save()

                except IntegrityError as e:
                    print(e, flush=True)
                    import traceback
                    traceback.print_exc()
                    return ClaimTaskMutation(success=False, message='There was a problem claiming this bounty', is_need_agreement=False)

            notification.tasks.send_notification.delay([Notification.Type.EMAIL],
                                                       Notification.EventType.TASK_CLAIMED,
                                                       receivers=list(
                                                           {challenge.created_by.id, challenge.reviewer.id, current_person.id}),
                                                       task_id=challenge.id,
                                                       user=current_person.slug)
        except Challenge.DoesNotExist:
            success = False
            message = "The challenge doesn't exist"
        except Bounty.DoesNotExist:
            success = False
            message = "The Bounty doesn't exist"
        except Person.DoesNotExist:
            success = False
            message = "The person doesn't exist"

        return ClaimTaskMutation(success=success, message=message, is_need_agreement=is_need_agreement)


class RejectTaskMutation(InfoStatusMutation, graphene.Mutation):
    class Arguments:
        task_id = graphene.Int(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        try:
            task_id = kwargs.get("task_id")
            task = Task.objects.get(id=task_id)

            # check if user has permissions
            if not is_admin_or_manager(current_person, task.product.slug):
                return RejectTaskMutation(success=False, message="You don't have permissions")

            # set "Failed" status to task claims
            task_claim = task.taskclaim_set.filter(kind__in=[CLAIM_TYPE_IN_REVIEW]).first()
            task_claim.kind = CLAIM_TYPE_FAILED
            task_claim.save()

            # set task status "Available"
            task.status = Task.TASK_STATUS_AVAILABLE
            task.updated_by = current_person
            task.updated_at = datetime.now()
            task.save()

            notification.tasks.send_notification.delay([Notification.Type.EMAIL],
                                                       Notification.EventType.SUBMISSION_REJECTED,
                                                       receivers=list(
                                                           {task.created_by.id, task.reviewer.id, 
                                                            current_person.id, task_claim.person.id}),
                                                       task_id=task.id,
                                                       user=task_claim.person.slug)            

            return RejectTaskMutation(success=True, message="The work has been rejected")
        except Task.DoesNotExist:
            return RejectTaskMutation(success=False, message="The task doesn't exist")


class RequestRevisionTaskMutation(InfoStatusMutation, graphene.Mutation):
    class Arguments:
        task_id = graphene.Int(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        try:
            task_id = kwargs.get("task_id")
            task = Task.objects.get(id=task_id)

            # check if user has permissions
            if not is_admin_or_manager(current_person, task.product.slug):
                return RequestRevisionTaskMutation(success=False, message="You don't have permissions")

            # set "Failed" status to task claims
            task_claim = task.taskclaim_set.filter(kind__in=[CLAIM_TYPE_IN_REVIEW]).first()
            task_claim.kind = CLAIM_TYPE_ACTIVE
            task_claim.save()

            # set task status "Claimed"
            task.status = Task.TASK_STATUS_CLAIMED
            task.updated_by = current_person
            task.updated_at = datetime.now()
            task.save()

            notification.tasks.send_notification.delay([Notification.Type.EMAIL],
                                                       Notification.EventType.SUBMISSION_REVISION_REQUESTED,
                                                       receivers=list(
                                                           {task.created_by.id, task.reviewer.id, 
                                                           current_person.id, task_claim.person.id}),
                                                       task_id=task.id,
                                                       user=task_claim.person.slug)            

            return RequestRevisionTaskMutation(success=True, message="The work has been requested for revision")
        except Task.DoesNotExist:
            return RequestRevisionTaskMutation(success=False, message="The task doesn't exist")


class ApproveTaskMutation(InfoStatusMutation, graphene.Mutation):
    class Arguments:
        task_id = graphene.Int(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, **kwargs):
        try:
            task_id = kwargs.get("task_id")
            task = Task.objects.get(id=task_id)

            # check if user has permissions
            if not is_admin_or_manager(current_person, task.product.slug):
                return ApproveTaskMutation(success=False, message="You don't have permissions")

            # set "Done" status to task claims
            task_claim = task.taskclaim_set.filter(kind__in=[CLAIM_TYPE_IN_REVIEW]).first()
            task_claim.kind = CLAIM_TYPE_DONE
            task_claim.save()

            # set task status "Done"
            task.status = Task.TASK_STATUS_DONE
            task.updated_by = current_person
            task.updated_at = datetime.now()
            task.save()
            notification.tasks.send_notification.delay([Notification.Type.EMAIL],
                                                       Notification.EventType.SUBMISSION_APPROVED,
                                                       receivers=list(
                                                           {task.created_by.id, task.reviewer.id, 
                                                            current_person.id, task_claim.person.id}),
                                                       task_id=task.id,
                                                       user=task_claim.person.slug)

            return ApproveTaskMutation(success=True, message="The work has been approved")
        except Task.DoesNotExist:
            return ApproveTaskMutation(success=False, message="The task doesn't exist")


class CreateProductRequestMutation(graphene.Mutation, InfoType):
    class Arguments:
        file = Upload(required=False)
        product_input = ProductInput(required=True)

    @staticmethod
    @is_current_person
    def mutate(current_person, info, *args, product_input, file=None):

        # check if user is in development mode
        try:
            validate_development_edition("product")
        except ValidationError as e:
            return CreateProductRequestMutation(status=False, message=str(e))

        url = None
        product_name = product_input.name

        if file:
            url = upload_photo(file, 'products')

        products = Product.objects.filter(name=product_name).exists()
        product_requests = CreateProductRequest.objects.filter(name=product_name).exists()

        if products or product_requests:
            return CreateProductRequestMutation(status=False, message="Product name already exists")

        CreateProductRequest.objects.create(
            photo=url,
            name=product_name,
            short_description=product_input.short_description,
            full_description=product_input.full_description,
            website=product_input.website,
            video_url=product_input.get("video_url", None),
            is_private=product_input.get("is_private", False),
            created_by=current_person
        )

        return CreateProductMutation(status=True, message="Create product request has been successfully created")


class WorkMutations(graphene.ObjectType):
    create_product = CreateProductMutation.Field()
    update_product = UpdateProductMutation.Field()
    create_product_request = CreateProductRequestMutation.Field()
    delete_product = DeleteProductMutation.Field()
    update_capability_tree = UpdateCapabilityTreeMutation.Field()
    create_capability = CreateCapabilityMutation.Field()
    update_capability = UpdateCapabilityMutation.Field()
    delete_capability = DeleteCapabilityMutation.Field()
    create_initiative = CreateInitiativeMutation.Field()
    update_initiative = UpdateInitiativeMutation.Field()
    delete_initiative = DeleteInitiativeMutation.Field()
    create_challenge = CreateChallengeMutation.Field()
    update_challenge = UpdateChallengeMutation.Field()
    delete_challenge = DeleteChallengeMutation.Field()
    create_code_repository = CreateCodeRepositoryMutation.Field()
    create_attachment = CreateAttachmentMutation.Field()
    delete_attachment = DeleteAttachmentMutation.Field()
    change_task_priority = ChangeTaskPriorityMutation.Field()
    leave_task = LeaveTaskMutation.Field()
    claim_task = ClaimTaskMutation.Field()
    in_review_task = InReviewTaskMutation.Field()
    approve_task = ApproveTaskMutation.Field()
    reject_task = RejectTaskMutation.Field()
    request_revision_task = RequestRevisionTaskMutation.Field()
